"""Create filing sections and chunks.

Revision ID: 20260815_0003
Revises: 20260815_0002
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0003"
down_revision: str | None = "20260815_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("filings", sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "filing_sections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("section_name", sa.String(length=100), nullable=False),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("filing_id", "section_order"),
    )
    op.create_index(
        op.f("ix_filing_sections_filing_id"), "filing_sections", ["filing_id"], unique=False
    )
    op.create_table(
        "filing_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("section_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["section_id"], ["filing_sections.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("section_id", "chunk_index"),
    )
    op.create_index(
        op.f("ix_filing_chunks_section_id"), "filing_chunks", ["section_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_filing_chunks_section_id"), table_name="filing_chunks")
    op.drop_table("filing_chunks")
    op.drop_index(op.f("ix_filing_sections_filing_id"), table_name="filing_sections")
    op.drop_table("filing_sections")
    op.drop_column("filings", "ingested_at")
