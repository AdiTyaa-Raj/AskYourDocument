"""Persist extracted text for S3-hosted documents."""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.document_text_extraction import DocumentTextExtraction
from app.services.pdfplumber_text_extraction_service import (
    PdfplumberExtractionError,
    PdfplumberNotSupportedError,
    extract_text_from_s3_pdf,
)

_TENANT_PREFIX = re.compile(r"^tenant-(?P<tenant_id>[0-9]+)/")


def _parse_bool(value: Optional[str], *, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def is_pdfplumber_enabled_on_upload() -> bool:
    return _parse_bool(os.getenv("PDFPLUMBER_ON_UPLOAD"), default=True)


def _infer_tenant_id_from_key(key: str) -> Optional[int]:
    match = _TENANT_PREFIX.match((key or "").lstrip("/"))
    if not match:
        return None
    try:
        tenant_id = int(match.group("tenant_id"))
        return tenant_id if tenant_id > 0 else None
    except Exception:
        return None


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "…"


def extract_and_store_text_pdfplumber(
    *,
    db: Session,
    bucket: str,
    key: str,
    s3_uri: Optional[str] = None,
    tenant_id: Optional[int] = None,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    size_bytes: Optional[int] = None,
    force: bool = False,
) -> DocumentTextExtraction:
    method = "pdfplumber"
    s3_uri_value = s3_uri or f"s3://{bucket}/{key}"
    tenant_id_value = tenant_id if (tenant_id is not None and tenant_id > 0) else _infer_tenant_id_from_key(key)

    existing = (
        db.query(DocumentTextExtraction)
        .filter(
            DocumentTextExtraction.bucket == bucket,
            DocumentTextExtraction.key == key,
            DocumentTextExtraction.extraction_method == method,
        )
        .first()
    )

    if existing and existing.status == "SUCCEEDED" and not force:
        return existing

    record = existing or DocumentTextExtraction(
        tenant_id=tenant_id_value,
        bucket=bucket,
        key=key,
        s3_uri=s3_uri_value,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        extraction_method=method,
        status="PENDING",
        extracted_text=None,
        extracted_char_count=0,
        error_message=None,
        extracted_at=None,
    )

    if existing is None:
        db.add(record)
        db.flush()

    now = datetime.now(timezone.utc)
    try:
        extracted_text = extract_text_from_s3_pdf(
            bucket=bucket,
            key=key,
            filename=filename,
            content_type=content_type,
        )
        record.status = "SUCCEEDED"
        record.extracted_text = extracted_text
        record.extracted_char_count = int(len(extracted_text or ""))
        record.error_message = None
        record.extracted_at = now
    except PdfplumberNotSupportedError as exc:
        record.status = "SKIPPED"
        record.extracted_text = None
        record.extracted_char_count = 0
        record.error_message = _truncate(str(exc) or "Unsupported document", 1024)
        record.extracted_at = now
    except (PdfplumberExtractionError, Exception) as exc:
        record.status = "FAILED"
        record.extracted_text = None
        record.extracted_char_count = 0
        record.error_message = _truncate(str(exc) or "Extraction failed", 1024)
        record.extracted_at = now

    db.commit()
    db.refresh(record)
    return record
