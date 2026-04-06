"""Add document_jobs table for async pipeline processing.

Revision ID: e5f3a2b1c4d6
Revises: d4e2f9a3b1c5
Create Date: 2026-04-05

Each row represents one pending or failed pipeline stage (text_extraction,
chunking, embedding) for a document.  Rows are deleted on success so the
table only ever holds work that still needs doing.
"""

from alembic import op
import sqlalchemy as sa

revision = "e5f3a2b1c4d6"
down_revision = "d4e2f9a3b1c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("bucket", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=2048), nullable=False),
        sa.Column("s3_uri", sa.String(length=2300), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("document_text_extraction_id", sa.Integer(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
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
        sa.ForeignKeyConstraint(
            ["document_text_extraction_id"],
            ["document_text_extractions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_document_jobs_id", "document_jobs", ["id"], unique=False)
    op.create_index("ix_document_jobs_tenant_id", "document_jobs", ["tenant_id"], unique=False)
    op.create_index("ix_document_jobs_job_type", "document_jobs", ["job_type"], unique=False)
    op.create_index("ix_document_jobs_status", "document_jobs", ["status"], unique=False)
    op.create_index(
        "ix_document_jobs_extraction_id",
        "document_jobs",
        ["document_text_extraction_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_document_jobs_extraction_id", table_name="document_jobs")
    op.drop_index("ix_document_jobs_status", table_name="document_jobs")
    op.drop_index("ix_document_jobs_job_type", table_name="document_jobs")
    op.drop_index("ix_document_jobs_tenant_id", table_name="document_jobs")
    op.drop_index("ix_document_jobs_id", table_name="document_jobs")
    op.drop_table("document_jobs")
