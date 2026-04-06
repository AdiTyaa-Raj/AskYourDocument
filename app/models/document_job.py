"""Document processing job queue model."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.config.db import Base

# job_type values
JOB_TYPE_TEXT_EXTRACTION = "text_extraction"
JOB_TYPE_CHUNKING = "chunking"
JOB_TYPE_EMBEDDING = "embedding"

# status values
JOB_STATUS_PENDING = "pending"
JOB_STATUS_IN_PROGRESS = "in_progress"
JOB_STATUS_FAILED = "failed"


class DocumentJob(Base):
    """One row per pending pipeline stage for a document.

    Pipeline order: text_extraction → chunking → embedding.
    Rows are deleted when the job succeeds; only pending/failed jobs remain.
    """

    __tablename__ = "document_jobs"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Pipeline stage
    job_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)

    # pending → in_progress → (deleted on success) | failed
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JOB_STATUS_PENDING, index=True
    )

    # S3 coordinates — needed by every stage
    bucket: Mapped[str] = mapped_column(String(255), nullable=False)
    key: Mapped[str] = mapped_column(String(2048), nullable=False)
    s3_uri: Mapped[str] = mapped_column(String(2300), nullable=False)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    content_type: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Populated after text_extraction completes; required by chunking / embedding
    document_text_extraction_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("document_text_extractions.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
