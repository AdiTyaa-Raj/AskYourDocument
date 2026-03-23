"""PDF text extraction helpers using pdfplumber.

This module extracts text from PDFs stored in S3. It is designed to be safe to
call from scripts/maintenance tasks without crashing the process on malformed
PDFs.
"""

from __future__ import annotations
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Optional

import pdfplumber

from app.services.s3_storage_service import download_s3_object_to_fileobj


class PdfplumberNotSupportedError(RuntimeError):
    pass


class PdfplumberExtractionError(RuntimeError):
    pass


def _parse_int(value: Optional[str], *, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
        return parsed if parsed > 0 else default
    except Exception:
        return default


@dataclass(frozen=True)
class PdfplumberSettings:
    max_pages: int
    spooled_max_bytes: int


def get_pdfplumber_settings() -> PdfplumberSettings:
    return PdfplumberSettings(
        max_pages=_parse_int(os.getenv("PDFPLUMBER_MAX_PAGES"), default=500),
        spooled_max_bytes=_parse_int(os.getenv("PDFPLUMBER_SPOOLED_MAX_BYTES"), default=50 * 1024 * 1024),
    )


def _is_pdf(filename: Optional[str], content_type: Optional[str]) -> bool:
    if content_type and "pdf" in content_type.lower():
        return True
    if filename:
        return filename.strip().lower().endswith(".pdf")
    return False


def extract_text_from_s3_pdf(
    *,
    bucket: str,
    key: str,
    filename: Optional[str] = None,
    content_type: Optional[str] = None,
    settings: Optional[PdfplumberSettings] = None,
) -> str:
    cfg = settings or get_pdfplumber_settings()
    if not _is_pdf(filename, content_type):
        raise PdfplumberNotSupportedError("Unsupported file type for pdfplumber text extraction")

    spooled = tempfile.SpooledTemporaryFile(max_size=cfg.spooled_max_bytes)
    try:
        download_s3_object_to_fileobj(bucket=bucket, key=key, file_obj=spooled)
        spooled.seek(0)
        with pdfplumber.open(spooled) as pdf:
            texts: list[str] = []
            total_pages = len(pdf.pages)
            max_pages = min(cfg.max_pages, total_pages) if cfg.max_pages else total_pages
            for idx in range(max_pages):
                page = pdf.pages[idx]
                page_text = page.extract_text() or ""
                page_text = re.sub(r"[ \t]+\n", "\n", page_text).strip()
                if page_text:
                    texts.append(page_text)
            return "\n\n".join(texts).strip()
    except PdfplumberNotSupportedError:
        raise
    except Exception as exc:
        raise PdfplumberExtractionError("Failed to extract text from PDF") from exc
    finally:
        try:
            spooled.close()
        except Exception:
            pass
