"""AWS Textract text extraction helpers.

This module is intentionally minimal: it supports extracting plain text from
uploaded PDFs/images stored in S3 and is safe to call from request handlers.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError, EndpointConnectionError

from app.services.pdfplumber_text_extraction_service import (
    PdfplumberExtractionError,
    PdfplumberNotSupportedError,
    extract_text_from_s3_pdf,
)


logger = logging.getLogger(__name__)


class TextractNotSupportedError(RuntimeError):
    pass


class TextractExtractionError(RuntimeError):
    pass


class TextractConnectivityError(TextractExtractionError):
    pass


class TextractTimeoutError(TextractExtractionError):
    pass


def _parse_bool(value: Optional[str], *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _safe_int(value: Optional[str], *, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
        return parsed if parsed > 0 else default
    except Exception:
        return default


@dataclass(frozen=True)
class TextractSettings:
    enabled_on_upload: bool
    log_extracted_text: bool
    log_char_limit: int
    poll_interval_seconds: int
    job_timeout_seconds: int
    region: str
    endpoint_url: Optional[str]
    access_key_id: Optional[str]
    secret_access_key: Optional[str]
    session_token: Optional[str]


def get_textract_settings() -> TextractSettings:
    region = (
        os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("TEXTRACT_REGION")
        or "us-east-1"
    )
    region = region.strip() or "us-east-1"
    endpoint_url = (os.getenv("TEXTRACT_ENDPOINT_URL") or "").strip() or None
    return TextractSettings(
        enabled_on_upload=_parse_bool(os.getenv("TEXTRACT_ON_UPLOAD"), default=True),
        log_extracted_text=_parse_bool(os.getenv("TEXTRACT_LOG_TEXT"), default=True),
        log_char_limit=_safe_int(os.getenv("TEXTRACT_LOG_CHAR_LIMIT"), default=5000),
        poll_interval_seconds=_safe_int(os.getenv("TEXTRACT_POLL_INTERVAL_SECONDS"), default=3),
        job_timeout_seconds=_safe_int(os.getenv("TEXTRACT_JOB_TIMEOUT_SECONDS"), default=90),
        region=region,
        endpoint_url=endpoint_url,
        access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("ACCESS_KEY_ID") or None,
        secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("SECRET_ACCESS_KEY") or None,
        session_token=os.getenv("AWS_SESSION_TOKEN") or None,
    )


@lru_cache(maxsize=1)
def _get_textract_client(settings: TextractSettings):
    session = boto3.session.Session(
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=settings.secret_access_key,
        aws_session_token=settings.session_token,
        region_name=settings.region,
    )
    boto_cfg = BotoConfig(retries={"max_attempts": 3, "mode": "standard"})
    return session.client(
        "textract",
        region_name=settings.region,
        endpoint_url=settings.endpoint_url,
        config=boto_cfg,
    )


def _is_pdf(filename: str, content_type: Optional[str]) -> bool:
    if content_type and "pdf" in content_type.lower():
        return True
    return filename.strip().lower().endswith(".pdf")


def _is_supported_image(filename: str, content_type: Optional[str]) -> bool:
    if content_type and content_type.lower().startswith("image/"):
        return True
    lowered = filename.strip().lower()
    return lowered.endswith((".png", ".jpg", ".jpeg", ".tif", ".tiff"))


def _extract_lines_from_blocks(blocks: list[dict]) -> str:
    lines: list[str] = []
    for block in blocks or []:
        if block.get("BlockType") == "LINE" and block.get("Text"):
            lines.append(str(block["Text"]))
    return "\n".join(lines).strip()


def extract_text_from_s3(
    *,
    bucket: str,
    key: str,
    filename: str,
    content_type: Optional[str],
    settings: Optional[TextractSettings] = None,
) -> str:
    """Extract plain text from an S3 object using AWS Textract.

    - PDFs: uses asynchronous `StartDocumentTextDetection` and polls.
    - Images: uses synchronous `DetectDocumentText`.
    """

    cfg = settings or get_textract_settings()
    client = _get_textract_client(cfg)

    try:
        if _is_pdf(filename, content_type):
            start_resp = client.start_document_text_detection(
                DocumentLocation={"S3Object": {"Bucket": bucket, "Name": key}},
            )
            job_id = start_resp["JobId"]

            deadline = time.monotonic() + cfg.job_timeout_seconds
            while True:
                if time.monotonic() > deadline:
                    raise TextractTimeoutError(
                        f"Timed out waiting for Textract job to finish (job_id={job_id})"
                    )
                resp = client.get_document_text_detection(JobId=job_id)
                status = resp.get("JobStatus")
                if status == "SUCCEEDED":
                    blocks: list[dict] = list(resp.get("Blocks") or [])
                    next_token = resp.get("NextToken")
                    while next_token:
                        page = client.get_document_text_detection(JobId=job_id, NextToken=next_token)
                        blocks.extend(page.get("Blocks") or [])
                        next_token = page.get("NextToken")
                    return _extract_lines_from_blocks(blocks)
                if status == "FAILED":
                    message = resp.get("StatusMessage") or "Unknown error"
                    raise TextractExtractionError(f"Textract job failed: {message} (job_id={job_id})")

                time.sleep(cfg.poll_interval_seconds)

        if _is_supported_image(filename, content_type):
            resp = client.detect_document_text(
                Document={"S3Object": {"Bucket": bucket, "Name": key}},
            )
            return _extract_lines_from_blocks(list(resp.get("Blocks") or []))

        raise TextractNotSupportedError("Unsupported file type for Textract text extraction")
    except EndpointConnectionError as exc:
        raise TextractConnectivityError(
            f"Could not reach AWS Textract endpoint (region={cfg.region})"
        ) from exc
    except TextractExtractionError:
        raise
    except (BotoCoreError, ClientError) as exc:
        raise TextractExtractionError(f"Textract call failed: {type(exc).__name__}") from exc


def maybe_extract_text_and_log(
    *,
    bucket: str,
    key: str,
    s3_uri: str,
    filename: str,
    content_type: Optional[str],
) -> None:
    cfg = get_textract_settings()
    if not cfg.enabled_on_upload:
        return

    method = "textract"
    try:
        extracted_text = extract_text_from_s3(
            bucket=bucket,
            key=key,
            filename=filename,
            content_type=content_type,
            settings=cfg,
        )
    except TextractNotSupportedError:
        logger.info("Textract skipped unsupported upload", extra={"s3_uri": s3_uri})
        return
    except TextractConnectivityError as exc:
        if _is_pdf(filename, content_type):
            try:
                extracted_text = extract_text_from_s3_pdf(
                    bucket=bucket,
                    key=key,
                    filename=filename,
                    content_type=content_type,
                )
                method = "pdfplumber-fallback"
            except (PdfplumberNotSupportedError, PdfplumberExtractionError) as fallback_exc:
                logger.warning(
                    "Textract unavailable and fallback extraction failed",
                    extra={"s3_uri": s3_uri, "error": str(fallback_exc)},
                )
                return
        else:
            logger.warning(
                "Textract unavailable (connectivity)",
                extra={"s3_uri": s3_uri, "error": str(exc)},
            )
            return
    except Exception:
        logger.exception("Textract text extraction failed", extra={"s3_uri": s3_uri})
        return

    if not cfg.log_extracted_text:
        return

    if not extracted_text:
        logger.info("%s extracted empty text", method, extra={"s3_uri": s3_uri})
        return

    preview = extracted_text[: cfg.log_char_limit] if cfg.log_char_limit else extracted_text
    suffix = "" if len(preview) == len(extracted_text) else f"\n… (truncated; {len(extracted_text)} chars total)"
    logger.info("%s extracted text for %s:\n%s%s", method, s3_uri, preview, suffix)
