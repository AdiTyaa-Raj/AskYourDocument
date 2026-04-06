"""Background worker that polls document_jobs and processes each pipeline stage.

Pipeline order:  text_extraction → chunking → embedding

The worker runs in a daemon thread started at application startup.  It polls
the database every JOB_POLL_INTERVAL_SECONDS (default 5) for pending jobs,
marks each one in_progress, runs the corresponding handler, then:

  • On success  – deletes the completed job row and inserts the next stage.
  • On failure  – records the error and marks the job as 'failed'.

Only rows with status='pending' are picked up; 'failed' rows stay in the
table for inspection / manual retry.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional

from sqlalchemy.orm import Session

from app.models.document_job import (
    JOB_STATUS_FAILED,
    JOB_STATUS_IN_PROGRESS,
    JOB_STATUS_PENDING,
    JOB_TYPE_CHUNKING,
    JOB_TYPE_EMBEDDING,
    JOB_TYPE_TEXT_EXTRACTION,
    DocumentJob,
)
from app.models.document_text_extraction import DocumentTextExtraction

logger = logging.getLogger(__name__)

_POLL_INTERVAL = int(os.getenv("JOB_POLL_INTERVAL_SECONDS", "5"))
_MAX_ATTEMPTS  = int(os.getenv("JOB_MAX_ATTEMPTS", "3"))

# Human-readable labels used in log lines
_JOB_LABEL = {
    JOB_TYPE_TEXT_EXTRACTION: "TEXT-EXTRACTION",
    JOB_TYPE_CHUNKING:        "CHUNKING",
    JOB_TYPE_EMBEDDING:       "EMBEDDING",
}


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

def _log_start(job: DocumentJob) -> None:
    label = _JOB_LABEL.get(job.job_type, job.job_type.upper())
    logger.info(
        "┌─ [JOB STARTED] %s | job_id=%-4s  attempt=%d/%d | file=%s | s3=%s",
        label,
        job.id,
        job.attempt_count,
        _MAX_ATTEMPTS,
        job.filename or "(unknown)",
        job.s3_uri,
    )


def _log_success(job: DocumentJob, elapsed: float, detail: str = "") -> None:
    label = _JOB_LABEL.get(job.job_type, job.job_type.upper())
    logger.info(
        "└─ [JOB COMPLETED] %s | job_id=%-4s  elapsed=%.2fs | file=%s%s",
        label,
        job.id,
        elapsed,
        job.filename or "(unknown)",
        f" | {detail}" if detail else "",
    )


def _log_queued(next_job_type: str, filename: Optional[str], s3_uri: str) -> None:
    label = _JOB_LABEL.get(next_job_type, next_job_type.upper())
    logger.info(
        "   [JOB QUEUED]  %s | file=%s | s3=%s",
        label,
        filename or "(unknown)",
        s3_uri,
    )


def _log_status_flag(extraction_id: int, flag: str, filename: Optional[str]) -> None:
    logger.info(
        "   [STATUS UPDATED] extraction_id=%-4s  %s=True  | file=%s",
        extraction_id,
        flag,
        filename or "(unknown)",
    )


def _log_failure(job: DocumentJob, elapsed: float, exc: Exception, will_retry: bool) -> None:
    label = _JOB_LABEL.get(job.job_type, job.job_type.upper())
    outcome = f"retrying (attempt {job.attempt_count}/{_MAX_ATTEMPTS})" if will_retry else "giving up (max attempts reached)"
    logger.error(
        "└─ [JOB FAILED]  %s | job_id=%-4s  elapsed=%.2fs | %s | file=%s | error=%s",
        label,
        job.id,
        elapsed,
        outcome,
        job.filename or "(unknown)",
        exc,
    )


# ---------------------------------------------------------------------------
# Handlers – one per job_type
# ---------------------------------------------------------------------------


def _handle_text_extraction(job: DocumentJob, db: Session, elapsed_ref: list) -> None:
    """Run pdfplumber extraction; on success insert a chunking job."""
    from app.services.document_text_extraction_service import extract_and_store_text_pdfplumber

    t0 = time.perf_counter()
    extraction = extract_and_store_text_pdfplumber(
        db=db,
        tenant_id=job.tenant_id,
        bucket=job.bucket,
        key=job.key,
        s3_uri=job.s3_uri,
        filename=job.filename,
        content_type=job.content_type,
        size_bytes=job.size_bytes,
    )
    elapsed_ref[0] = time.perf_counter() - t0

    # Mark extraction stage complete on the record
    extraction.extraction_completed = True

    detail = (
        f"extraction_id={extraction.id}  status={extraction.status}"
        f"  chars={extraction.extracted_char_count:,}"
    )

    # Delete the completed job and queue next stage
    db.delete(job)
    next_job = DocumentJob(
        tenant_id=job.tenant_id,
        job_type=JOB_TYPE_CHUNKING,
        status=JOB_STATUS_PENDING,
        bucket=job.bucket,
        key=job.key,
        s3_uri=job.s3_uri,
        filename=job.filename,
        content_type=job.content_type,
        size_bytes=job.size_bytes,
        document_text_extraction_id=extraction.id,
    )
    db.add(next_job)
    db.commit()

    _log_success(job, elapsed_ref[0], detail)
    _log_status_flag(extraction.id, "extraction_completed", job.filename)
    _log_queued(JOB_TYPE_CHUNKING, job.filename, job.s3_uri)


def _handle_chunking(job: DocumentJob, db: Session, elapsed_ref: list) -> None:
    """Run chunking; on success insert an embedding job."""
    from app.services.document_chunking_service import chunk_and_store

    extraction: Optional[DocumentTextExtraction] = None
    if job.document_text_extraction_id:
        extraction = (
            db.query(DocumentTextExtraction)
            .filter(DocumentTextExtraction.id == job.document_text_extraction_id)
            .first()
        )

    if extraction is None:
        raise RuntimeError(
            f"DocumentTextExtraction {job.document_text_extraction_id} not found for job {job.id}"
        )

    t0 = time.perf_counter()
    chunks = chunk_and_store(db=db, extraction=extraction)
    elapsed_ref[0] = time.perf_counter() - t0

    # Mark chunking stage complete on the record
    extraction.chunking_completed = True

    detail = f"extraction_id={extraction.id}  chunks_produced={len(chunks)}"

    # Delete the completed job and queue next stage
    db.delete(job)
    next_job = DocumentJob(
        tenant_id=job.tenant_id,
        job_type=JOB_TYPE_EMBEDDING,
        status=JOB_STATUS_PENDING,
        bucket=job.bucket,
        key=job.key,
        s3_uri=job.s3_uri,
        filename=job.filename,
        content_type=job.content_type,
        size_bytes=job.size_bytes,
        document_text_extraction_id=job.document_text_extraction_id,
    )
    db.add(next_job)
    db.commit()

    _log_success(job, elapsed_ref[0], detail)
    _log_status_flag(extraction.id, "chunking_completed", job.filename)
    _log_queued(JOB_TYPE_EMBEDDING, job.filename, job.s3_uri)


def _handle_embedding(job: DocumentJob, db: Session, elapsed_ref: list) -> None:
    """Generate and store embeddings for document chunks using Google gemini-embedding-001."""
    import os
    from datetime import datetime, timezone

    from google import genai
    from google.genai import types as genai_types

    from app.models.document_chunk import DocumentChunk, EMBEDDING_MODEL_GOOGLE, EMBEDDING_DIM

    t0 = time.perf_counter()

    extraction: Optional[DocumentTextExtraction] = None
    if job.document_text_extraction_id:
        extraction = (
            db.query(DocumentTextExtraction)
            .filter(DocumentTextExtraction.id == job.document_text_extraction_id)
            .first()
        )

    if extraction is None:
        raise RuntimeError(
            f"DocumentTextExtraction {job.document_text_extraction_id} not found for job {job.id}"
        )

    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_text_extraction_id == extraction.id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )

    if not chunks:
        logger.warning(
            "   [EMBEDDING] job_id=%s  file=%s – no chunks found, skipping",
            job.id,
            job.filename or "(unknown)",
        )
    else:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY environment variable is not set")

        client = genai.Client(api_key=api_key)

        # Embed in batches of 100 (Google limit)
        BATCH_SIZE = 100
        now = datetime.now(timezone.utc)

        for batch_start in range(0, len(chunks), BATCH_SIZE):
            batch = chunks[batch_start: batch_start + BATCH_SIZE]
            texts = [c.chunk_text for c in batch]

            result = client.models.embed_content(
                model=EMBEDDING_MODEL_GOOGLE,
                contents=texts,
                config=genai_types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    output_dimensionality=EMBEDDING_DIM,
                ),
            )

            for chunk, emb_obj in zip(batch, result.embeddings):
                chunk.embedding = emb_obj.values
                chunk.embedding_model = EMBEDDING_MODEL_GOOGLE
                chunk.embedded_at = now

        db.flush()

    extraction.embedding_completed = True
    db.delete(job)
    db.commit()
    elapsed_ref[0] = time.perf_counter() - t0

    detail = f"extraction_id={extraction.id}  chunks_embedded={len(chunks)}"
    _log_success(job, elapsed_ref[0], detail)
    _log_status_flag(extraction.id, "embedding_completed", job.filename)


_HANDLERS = {
    JOB_TYPE_TEXT_EXTRACTION: _handle_text_extraction,
    JOB_TYPE_CHUNKING:        _handle_chunking,
    JOB_TYPE_EMBEDDING:       _handle_embedding,
}


# ---------------------------------------------------------------------------
# Core poll loop
# ---------------------------------------------------------------------------


def _process_one(db: Session) -> bool:
    """Fetch and process a single pending job.

    Uses SELECT … FOR UPDATE SKIP LOCKED so multiple worker instances (e.g.
    multiple uvicorn workers) never pick up the same row.

    Returns True if a job was processed (regardless of outcome).
    """
    job = (
        db.query(DocumentJob)
        .filter(DocumentJob.status == JOB_STATUS_PENDING)
        .order_by(DocumentJob.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )

    if job is None:
        return False

    # Claim the job
    job.status = JOB_STATUS_IN_PROGRESS
    job.attempt_count += 1
    db.commit()

    _log_start(job)

    handler = _HANDLERS.get(job.job_type)
    if handler is None:
        logger.error(
            "└─ [JOB FAILED]  UNKNOWN | job_id=%s  job_type=%s – no handler registered, marking failed",
            job.id,
            job.job_type,
        )
        job.status = JOB_STATUS_FAILED
        job.error_message = f"Unknown job_type: {job.job_type}"
        db.commit()
        return True

    elapsed_ref = [0.0]
    wall_start = time.perf_counter()

    try:
        handler(job, db, elapsed_ref)
    except Exception as exc:
        wall_elapsed = time.perf_counter() - wall_start
        will_retry = job.attempt_count < _MAX_ATTEMPTS
        _log_failure(job, wall_elapsed, exc, will_retry)
        logger.debug("Job id=%s exception detail:", job.id, exc_info=True)

        try:
            db.rollback()
            job.status = JOB_STATUS_PENDING if will_retry else JOB_STATUS_FAILED
            job.error_message = str(exc)[:2048]
            db.commit()
        except Exception:
            logger.exception("Failed to persist failure state for job_id=%s", job.id)

    return True


def _poll_loop(session_factory) -> None:
    """Main loop: drain the queue, then sleep."""
    logger.debug("[WORKER] Poll loop started (interval=%ds)", _POLL_INTERVAL)

    while True:
        try:
            db: Session = session_factory()
            try:
                processed = 0
                while _process_one(db):
                    processed += 1
                if processed:
                    logger.info(
                        "[WORKER] Processed %d job(s) this cycle", processed
                    )
            finally:
                db.close()
        except Exception:
            logger.exception("[WORKER] Unexpected error in poll loop")

        time.sleep(_POLL_INTERVAL)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def start_worker() -> threading.Thread:
    """Start the background polling thread.  Call once at app startup."""
    from app.config.db import get_session_factory

    thread = threading.Thread(
        target=_poll_loop,
        args=(get_session_factory(),),
        daemon=True,
        name="document-job-worker",
    )
    thread.start()
    logger.info(
        "━━━ [WORKER STARTED] Document job worker running "
        "(poll_interval=%ds  max_attempts=%d) ━━━",
        _POLL_INTERVAL,
        _MAX_ATTEMPTS,
    )
    return thread
