"""S3 upload service."""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterator, Optional

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from app.config.s3 import S3Config, get_s3_config

_FILENAME_ALLOWED = re.compile(r"[^a-zA-Z0-9._-]+")


class S3NotConfiguredError(RuntimeError):
    pass


class S3UploadError(RuntimeError):
    pass


class S3DownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class S3UploadResult:
    bucket: str
    key: str
    etag: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    s3_uri: str


def _sanitize_filename(filename: str) -> str:
    base = os.path.basename(filename.strip())
    if not base:
        return "upload"
    sanitized = _FILENAME_ALLOWED.sub("-", base).strip("-.")
    return sanitized or "upload"


def _guess_size_bytes(file_obj) -> Optional[int]:
    try:
        current = file_obj.tell()
        file_obj.seek(0, os.SEEK_END)
        size = file_obj.tell()
        file_obj.seek(current, os.SEEK_SET)
        return int(size)
    except Exception:
        return None


@lru_cache(maxsize=1)
def _get_s3_client(cfg: S3Config):
    session = boto3.session.Session(
        aws_access_key_id=cfg.access_key_id,
        aws_secret_access_key=cfg.secret_access_key,
        aws_session_token=cfg.session_token,
        region_name=cfg.region,
    )

    boto_cfg = BotoConfig(
        retries={"max_attempts": 3, "mode": "standard"},
        s3={"addressing_style": "path"} if cfg.endpoint_url else {},
    )

    return session.client(
        "s3",
        region_name=cfg.region,
        endpoint_url=cfg.endpoint_url,
        config=boto_cfg,
    )


def upload_document_to_s3(
    *,
    file_obj,
    filename: str,
    content_type: Optional[str],
    tenant_id: Optional[int] = None,
    prefix: Optional[str] = None,
) -> S3UploadResult:
    cfg = get_s3_config()
    if cfg is None:
        raise S3NotConfiguredError(
            "Missing S3 configuration; set S3_BUCKET_NAME and AWS credentials in environment."
        )

    sanitized_name = _sanitize_filename(filename)
    upload_id = uuid.uuid4().hex

    key_parts: list[str] = []
    if tenant_id and tenant_id > 0:
        key_parts.append(f"tenant-{tenant_id}")
    if prefix:
        normalized = prefix.strip().strip("/")
        if normalized:
            key_parts.append(normalized)
    key_parts.append(f"{upload_id}-{sanitized_name}")
    key = "/".join(key_parts)

    try:
        try:
            file_obj.seek(0)
        except Exception:
            pass

        extra_args = {}
        if content_type:
            extra_args["ContentType"] = content_type

        client = _get_s3_client(cfg)
        response = client.upload_fileobj(
            Fileobj=file_obj,
            Bucket=cfg.bucket_name,
            Key=key,
            ExtraArgs=extra_args or None,
        )
        # upload_fileobj returns None on success; we don't get ETag unless we HEAD it.
        _ = response
    except (BotoCoreError, ClientError) as exc:
        raise S3UploadError("Failed to upload file to S3") from exc

    return S3UploadResult(
        bucket=cfg.bucket_name,
        key=key,
        etag=None,
        content_type=content_type,
        size_bytes=_guess_size_bytes(file_obj),
        s3_uri=f"s3://{cfg.bucket_name}/{key}",
    )


def download_s3_object_to_fileobj(*, bucket: str, key: str, file_obj) -> None:
    cfg = get_s3_config()
    if cfg is None:
        raise S3NotConfiguredError(
            "Missing S3 configuration; set S3_BUCKET_NAME and AWS credentials in environment."
        )

    try:
        client = _get_s3_client(cfg)
        client.download_fileobj(Bucket=bucket, Key=key, Fileobj=file_obj)
    except (BotoCoreError, ClientError) as exc:
        raise S3DownloadError("Failed to download file from S3") from exc


def iter_s3_keys(*, bucket: str, prefix: str | None = None) -> Iterator[str]:
    cfg = get_s3_config()
    if cfg is None:
        raise S3NotConfiguredError(
            "Missing S3 configuration; set S3_BUCKET_NAME and AWS credentials in environment."
        )

    try:
        client = _get_s3_client(cfg)
        paginator = client.get_paginator("list_objects_v2")
        kwargs = {"Bucket": bucket}
        if prefix:
            normalized = prefix.strip().lstrip("/")
            if normalized:
                kwargs["Prefix"] = normalized

        for page in paginator.paginate(**kwargs):
            for item in page.get("Contents") or []:
                key = item.get("Key")
                if key:
                    yield str(key)
    except (BotoCoreError, ClientError) as exc:
        raise S3DownloadError("Failed to list S3 keys") from exc
