from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import load_env
from app.config.db import bootstrap_schema, get_session_factory, is_db_configured
from app.config.s3 import get_s3_config
from app.services.document_text_extraction_service import extract_and_store_text_pdfplumber
from app.services.s3_storage_service import S3NotConfiguredError, iter_s3_keys


def _parse_int(value: str | None, *, default: int) -> int:
    try:
        parsed = int(value) if value is not None else default
        return parsed if parsed > 0 else default
    except Exception:
        return default


def main() -> int:
    load_env()

    parser = argparse.ArgumentParser(
        description="Extract text from PDFs stored in S3 (pdfplumber) and persist to the database."
    )
    parser.add_argument("--bucket", default=None, help="S3 bucket name (defaults to S3_BUCKET_NAME)")
    parser.add_argument("--prefix", default=None, help="Only process keys under this prefix")
    parser.add_argument("--limit", type=int, default=0, help="Max PDFs to process (0 = no limit)")
    parser.add_argument("--force", action="store_true", help="Re-extract even if already SUCCEEDED")
    args = parser.parse_args()

    if not is_db_configured():
        print("Database is not configured. Set DATABASE_URL or POSTGRES_* env vars.", file=sys.stderr)
        return 1

    s3_cfg = get_s3_config()
    if s3_cfg is None and not args.bucket:
        print("S3 is not configured. Set S3_BUCKET_NAME and AWS credentials in env vars.", file=sys.stderr)
        return 1

    bucket = (args.bucket or (s3_cfg.bucket_name if s3_cfg else None) or "").strip()
    if not bucket:
        print("Missing --bucket and S3_BUCKET_NAME is not set.", file=sys.stderr)
        return 1

    bootstrap_schema()
    session_factory = get_session_factory()

    processed = 0
    succeeded = 0
    failed = 0
    skipped = 0

    with session_factory() as db:
        try:
            for key in iter_s3_keys(bucket=bucket, prefix=args.prefix):
                lowered = key.lower()
                if not lowered.endswith(".pdf"):
                    continue

                if args.limit and processed >= args.limit:
                    break

                filename = os.path.basename(key)
                record = extract_and_store_text_pdfplumber(
                    db=db,
                    bucket=bucket,
                    key=key,
                    s3_uri=f"s3://{bucket}/{key}",
                    filename=filename,
                    content_type="application/pdf",
                    force=args.force,
                )

                processed += 1
                if record.status == "SUCCEEDED":
                    succeeded += 1
                elif record.status == "SKIPPED":
                    skipped += 1
                else:
                    failed += 1
        except S3NotConfiguredError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    print(
        f"Processed={processed} Succeeded={succeeded} Skipped={skipped} Failed={failed}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

