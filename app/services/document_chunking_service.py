"""Split extracted document text into chunks and persist them to the DB."""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.document_chunk import (
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    DEFAULT_CHUNK_SIZE_TOKENS,
    DEFAULT_MIN_CHUNK_TOKENS,
    DocumentChunk,
)
from app.models.document_text_extraction import DocumentTextExtraction

logger = logging.getLogger(__name__)


def chunk_and_store(
    *,
    db: Session,
    extraction: DocumentTextExtraction,
    chunk_size: int = DEFAULT_CHUNK_SIZE_TOKENS,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
    min_chunk_size: int = DEFAULT_MIN_CHUNK_TOKENS,
) -> list[DocumentChunk]:
    """Split *extraction.extracted_text* into chunks and save them.

    Skips silently if the extraction did not succeed or has no text.
    Deletes any previously stored chunks for this extraction before saving new ones.
    """
    if extraction.status != "SUCCEEDED" or not extraction.extracted_text:
        return []

    # Import here so the rest of the app doesn't hard-depend on langchain at import time.
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    char_chunk_size = chunk_size * 4
    char_chunk_overlap = chunk_overlap * 4
    min_chars = min_chunk_size * 4

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=char_chunk_size,
        chunk_overlap=char_chunk_overlap,
        length_function=len,
        separators=["\n\n\n", "\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""],
    )

    raw_chunks = splitter.split_text(extraction.extracted_text)

    # Drop chunks that are too small to be useful.
    chunks_text = [c for c in raw_chunks if len(c) >= min_chars]

    if not chunks_text:
        logger.warning(
            "No usable chunks produced for extraction_id=%s (s3_uri=%s)",
            extraction.id,
            extraction.s3_uri,
        )
        return []

    # Remove stale chunks from a previous run.
    db.query(DocumentChunk).filter(
        DocumentChunk.document_text_extraction_id == extraction.id
    ).delete(synchronize_session=False)

    records: list[DocumentChunk] = []
    for idx, text in enumerate(chunks_text):
        records.append(
            DocumentChunk(
                document_text_extraction_id=extraction.id,
                tenant_id=extraction.tenant_id,
                chunk_index=idx,
                chunk_text=text,
                char_count=len(text),
                token_count_estimate=len(text) // 4,
                chunk_size_tokens=chunk_size,
                chunk_overlap_tokens=chunk_overlap,
            )
        )

    db.add_all(records)
    db.commit()

    logger.info(
        "Stored %d chunks for extraction_id=%s (s3_uri=%s)",
        len(records),
        extraction.id,
        extraction.s3_uri,
    )
    return records
