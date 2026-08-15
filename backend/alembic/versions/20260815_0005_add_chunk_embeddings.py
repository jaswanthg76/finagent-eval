"""Add filing chunk embeddings.

Revision ID: 20260815_0005
Revises: 20260815_0004
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0005"
down_revision: str | None = "20260815_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.add_column(
        "filing_chunks",
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=512), nullable=True),
    )
    op.add_column(
        "filing_chunks", sa.Column("embedding_model", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "filing_chunks",
        sa.Column("embedding_content_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "filing_chunks", sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_filing_chunks_embedding_hnsw",
        "filing_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_filing_chunks_embedding_hnsw", table_name="filing_chunks")
    op.drop_column("filing_chunks", "embedded_at")
    op.drop_column("filing_chunks", "embedding_content_hash")
    op.drop_column("filing_chunks", "embedding_model")
    op.drop_column("filing_chunks", "embedding")

