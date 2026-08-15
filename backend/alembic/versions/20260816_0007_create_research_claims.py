"""Create research claims.

Revision ID: 20260816_0007
Revises: 20260816_0006
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260816_0007"
down_revision: str | None = "20260816_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_claims",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("claim_index", sa.Integer(), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=30), nullable=False),
        sa.Column("citation_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("extraction_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["report_id"], ["research_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("report_id", "claim_index"),
    )
    op.create_index(
        op.f("ix_research_claims_report_id"),
        "research_claims",
        ["report_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_research_claims_claim_type"),
        "research_claims",
        ["claim_type"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_research_claims_claim_type"), table_name="research_claims")
    op.drop_index(op.f("ix_research_claims_report_id"), table_name="research_claims")
    op.drop_table("research_claims")
