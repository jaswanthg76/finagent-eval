"""Create temporal evaluations.

Revision ID: 20260816_0011
Revises: 20260816_0010
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260816_0011"
down_revision: str | None = "20260816_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_temporal_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("checked_source_count", sa.Integer(), nullable=False),
        sa.Column(
            "violations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("verifier_version", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["report_id"], ["research_reports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_report_temporal_evaluations_report_id"),
        "report_temporal_evaluations",
        ["report_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_report_temporal_evaluations_report_id"),
        table_name="report_temporal_evaluations",
    )
    op.drop_table("report_temporal_evaluations")
