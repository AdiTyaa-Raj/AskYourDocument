"""AWS S3 configuration.

This project keeps configuration as module-level constants sourced from env vars.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class S3Config:
    bucket_name: str
    region: str
    endpoint_url: Optional[str]
    access_key_id: Optional[str]
    secret_access_key: Optional[str]
    session_token: Optional[str]


def get_s3_config() -> Optional[S3Config]:
    bucket_name = (
        os.getenv("S3_BUCKET_NAME")
        or os.getenv("AWS_S3_BUCKET")
        or os.getenv("AWS_BUCKET_NAME")
        or None
    )
    if not bucket_name:
        return None

    region = (
        os.getenv("AWS_DEFAULT_REGION")
        or os.getenv("AWS_REGION")
        or os.getenv("S3_REGION")
        or "us-east-1"
    )

    return S3Config(
        bucket_name=bucket_name,
        region=region,
        endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        access_key_id=os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("ACCESS_KEY_ID") or None,
        secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("SECRET_ACCESS_KEY") or None,
        session_token=os.getenv("AWS_SESSION_TOKEN") or None,
    )
