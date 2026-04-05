"""Model for document text chunks with embeddings, used for RAG."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.config.db import Base

# Embedding dimension for text-embedding-005 (Google Vertex AI)
EMBEDDING_DIM = 768

# Chunking defaults (DOCUMENT content type)
DEFAULT_CHUNK_SIZE_TOKENS = 500
DEFAULT_CHUNK_OVERLAP_TOKENS = 50
DEFAULT_MIN_CHUNK_TOKENS = 100

# text-embedding-005 model identifier
EMBEDDING_MODEL_TEXT_EMBEDDING_005 = "text-embedding-005"


class DocumentChunk(Base):
    """A chunk of text extracted from a document, optionally with its embedding vector."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "document_text_extraction_id",
            "chunk_index",
            name="uq_document_chunks_extraction_chunk_idx",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)

    # Parent document extraction
    document_text_extraction_id: Mapped[int] = mapped_column(
        ForeignKey("document_text_extractions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Tenant for multi-tenancy / access control
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Position of this chunk within the source document (0-based)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    # The chunk text content
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)

    # Size metrics
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count_estimate: Mapped[int] = mapped_column(Integer, nullable=False)  # char_count // 4

    # Chunking configuration used to produce this chunk (for reproducibility)
    chunk_size_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(DEFAULT_CHUNK_SIZE_TOKENS)
    )
    chunk_overlap_tokens: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(DEFAULT_CHUNK_OVERLAP_TOKENS)
    )

    # Embedding vector (text-embedding-005 → 768 dims)
    embedding: Mapped[Optional[list]] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    embedded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    document_text_extraction = relationship("DocumentTextExtraction", back_populates="chunks")
    tenant = relationship("Tenant")
