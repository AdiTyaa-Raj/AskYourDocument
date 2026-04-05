"""Add document_chunks table with pgvector embeddings.

Revision ID: c9d7e8f1a2b3
Revises: 3b6f4f8d2d9a
Create Date: 2026-04-05

Chunking config (DOCUMENT content type):
  chunk_size:    250 tokens  (1000 chars)
  chunk_overlap:  35 tokens  ( 140 chars)
  min_chunk:      70 tokens  ( 280 chars)

Embedding model: text-embedding-005 → 768 dimensions
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

revision = "c9d7e8f1a2b3"
down_revision = "3b6f4f8d2d9a"
branch_labels = None
depends_on = None

EMBEDDING_DIM = 768


def upgrade() -> None:
    # Enable pgvector extension (idempotent)
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), nullable=False),

        # FK to source extraction record; cascade delete so chunks are removed with the doc
        sa.Column("document_text_extraction_id", sa.Integer(), nullable=False),

        # Tenant for multi-tenancy
        sa.Column("tenant_id", sa.Integer(), nullable=True),

        # Position within the document (0-based)
        sa.Column("chunk_index", sa.Integer(), nullable=False),

        # Chunk content
        sa.Column("chunk_text", sa.Text(), nullable=False),

        # Size metrics
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("token_count_estimate", sa.Integer(), nullable=False),

        # Chunking config used (stored for reproducibility / re-chunking detection)
        sa.Column("chunk_size_tokens", sa.Integer(), server_default="250", nullable=False),
        sa.Column("chunk_overlap_tokens", sa.Integer(), server_default="35", nullable=False),

        # Embedding vector (text-embedding-005 → 768 dims); NULL until embedded
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=True),
        sa.Column("embedding_model", sa.String(length=100), nullable=True),
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),

        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),

        sa.ForeignKeyConstraint(
            ["document_text_extraction_id"],
            ["document_text_extractions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_text_extraction_id",
            "chunk_index",
            name="uq_document_chunks_extraction_chunk_idx",
        ),
    )

    op.create_index("ix_document_chunks_id", "document_chunks", ["id"], unique=False)
    op.create_index(
        "ix_document_chunks_document_text_extraction_id",
        "document_chunks",
        ["document_text_extraction_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_chunks_tenant_id", "document_chunks", ["tenant_id"], unique=False
    )

    # HNSW index for fast approximate nearest-neighbour search on the embedding column.
    # cosine distance is the standard metric for text-embedding-005.
    op.execute(
        """
        CREATE INDEX ix_document_chunks_embedding_hnsw
        ON document_chunks
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")
    op.drop_index("ix_document_chunks_tenant_id", table_name="document_chunks")
    op.drop_index(
        "ix_document_chunks_document_text_extraction_id", table_name="document_chunks"
    )
    op.drop_index("ix_document_chunks_id", table_name="document_chunks")
    op.drop_table("document_chunks")
