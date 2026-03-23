"""Add document_text_extractions table.

Revision ID: 3b6f4f8d2d9a
Revises: 14e176dd1c41
Create Date: 2026-03-23
"""

from alembic import op
import sqlalchemy as sa

revision = "3b6f4f8d2d9a"
down_revision = "14e176dd1c41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_text_extractions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=2048), nullable=False),
        sa.Column("s3_uri", sa.String(length=2300), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("extraction_method", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("extracted_char_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.String(length=1024), nullable=True),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "bucket",
            "key",
            "extraction_method",
            name="uq_doc_text_extract_bucket_key_method",
        ),
    )
    op.create_index("ix_document_text_extractions_id", "document_text_extractions", ["id"], unique=False)
    op.create_index(
        "ix_document_text_extractions_tenant_id",
        "document_text_extractions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_document_text_extractions_bucket_key",
        "document_text_extractions",
        ["bucket", "key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_text_extractions_bucket_key", table_name="document_text_extractions")
    op.drop_index("ix_document_text_extractions_tenant_id", table_name="document_text_extractions")
    op.drop_index("ix_document_text_extractions_id", table_name="document_text_extractions")
    op.drop_table("document_text_extractions")

