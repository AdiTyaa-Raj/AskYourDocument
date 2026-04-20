from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.config.db import get_db, is_db_configured
from app.middleware.auth import get_current_tenant_id, get_token_payload
from app.models.access_control import User
from app.models.document_job import DocumentJob, JOB_STATUS_PENDING, JOB_TYPE_TEXT_EXTRACTION
from app.models.document_text_extraction import DocumentTextExtraction
from app.services.s3_storage_service import S3NotConfiguredError, S3UploadError, upload_document_to_s3

router = APIRouter()
logger = logging.getLogger(__name__)


class UploadDocumentResponse(BaseModel):
    bucket: str
    key: str
    s3_uri: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None


class DocumentSummary(BaseModel):
    id: int
    filename: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    s3_uri: str
    status: str
    extraction_method: str
    extracted_char_count: int
    error_message: Optional[str]
    extraction_completed: bool
    chunking_completed: bool
    embedding_completed: bool
    tenant_id: Optional[int]
    extracted_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    documents: List[DocumentSummary]


@router.post(
    "/documents/upload",
    tags=["documents"],
    status_code=status.HTTP_201_CREATED,
    response_model=UploadDocumentResponse,
)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    prefix: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
) -> UploadDocumentResponse:
    payload = get_token_payload(request)
    is_super_admin = payload.get("is_super_admin", False)

    if is_super_admin:
        # Super admins are not scoped to any tenant; tenant_id stays None.
        tenant_id: Optional[int] = None
    else:
        # Fetch tenant_id from the user record in DB (authoritative source).
        email = payload.get("sub") or payload.get("email")
        user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User not found")
        tenant_id = user.tenant_id

    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")

    logger.info(
        "──────────────────────────────────────────────────────────────",
    )
    logger.info(
        "[UPLOAD] Received  file=%s  content_type=%s  tenant_id=%s",
        filename,
        file.content_type or "unknown",
        tenant_id,
    )

    try:
        result = upload_document_to_s3(
            file_obj=file.file,
            filename=filename,
            content_type=file.content_type,
            tenant_id=tenant_id,
            prefix=prefix,
        )
    except S3NotConfiguredError as exc:
        logger.error("[UPLOAD] FAILED (S3 not configured) | file=%s | %s", filename, exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except S3UploadError as exc:
        logger.error("[UPLOAD] FAILED (S3 error) | file=%s | %s", filename, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    logger.info(
        "[UPLOAD] S3 OK     file=%s  size=%s bytes  s3_uri=%s",
        filename,
        f"{result.size_bytes:,}" if result.size_bytes else "unknown",
        result.s3_uri,
    )

    # Queue async pipeline: text_extraction → chunking → embedding.
    if is_db_configured():
        job = DocumentJob(
            tenant_id=tenant_id,
            job_type=JOB_TYPE_TEXT_EXTRACTION,
            status=JOB_STATUS_PENDING,
            bucket=result.bucket,
            key=result.key,
            s3_uri=result.s3_uri,
            filename=filename,
            content_type=file.content_type,
            size_bytes=result.size_bytes,
        )
        db.add(job)
        db.commit()
        db.refresh(job)
        logger.info(
            "[UPLOAD] Job queued  job_id=%s  type=TEXT-EXTRACTION  file=%s → returning 201",
            job.id,
            filename,
        )
    else:
        logger.warning("[UPLOAD] DB not configured – pipeline job NOT queued for file=%s", filename)

    logger.info(
        "──────────────────────────────────────────────────────────────",
    )

    return UploadDocumentResponse(
        bucket=result.bucket,
        key=result.key,
        s3_uri=result.s3_uri,
        content_type=result.content_type,
        size_bytes=result.size_bytes,
    )


@router.get("/documents", tags=["documents"], response_model=DocumentListResponse)
def list_documents(
    request: Request,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    payload = get_token_payload(request)
    is_super_admin = payload.get("is_super_admin", False)

    query = db.query(DocumentTextExtraction)

    if not is_super_admin:
        tenant_id: Optional[int] = None
        try:
            tenant_id = get_current_tenant_id(request)
        except HTTPException:
            # Backward-compatible fallback for older tokens without tenant context:
            # derive tenant_id from the DB user record.
            email = payload.get("sub") or payload.get("email")
            user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
            if not user or not user.tenant_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="User tenant not found",
                )
            tenant_id = user.tenant_id

        query = query.filter(DocumentTextExtraction.tenant_id == tenant_id)

    total = query.count()
    documents = (
        query.order_by(DocumentTextExtraction.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return DocumentListResponse(
        total=total,
        skip=skip,
        limit=limit,
        documents=[DocumentSummary.model_validate(doc) for doc in documents],
    )
