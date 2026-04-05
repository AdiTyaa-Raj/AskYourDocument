"""Add extraction_completed, chunking_completed, embedding_completed flags to document_text_extractions.

Revision ID: d4e2f9a3b1c5
Revises: c9d7e8f1a2b3
Create Date: 2026-04-05
"""

from alembic import op
import sqlalchemy as sa

revision = "d4e2f9a3b1c5"
down_revision = "c9d7e8f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_text_extractions",
        sa.Column("extraction_completed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "document_text_extractions",
        sa.Column("chunking_completed", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "document_text_extractions",
        sa.Column("embedding_completed", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("document_text_extractions", "embedding_completed")
    op.drop_column("document_text_extractions", "chunking_completed")
    op.drop_column("document_text_extractions", "extraction_completed")
