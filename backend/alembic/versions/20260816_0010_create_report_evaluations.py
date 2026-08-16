"""Create report evaluations.

Revision ID: 20260816_0010
Revises: 20260816_0009
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260816_0010"
down_revision: str | None = "20260816_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("report_id", sa.Integer(), nullable=False),
        sa.Column("overall_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("grounding_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("numeric_accuracy_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("citation_score", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("temporal_integrity_score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("total_claim_count", sa.Integer(), nullable=False),
        sa.Column("evaluated_claim_count", sa.Integer(), nullable=False),
        sa.Column("verified_claim_count", sa.Integer(), nullable=False),
        sa.Column("partially_supported_claim_count", sa.Integer(), nullable=False),
        sa.Column("unsupported_claim_count", sa.Integer(), nullable=False),
        sa.Column("contradiction_count", sa.Integer(), nullable=False),
        sa.Column("error_count", sa.Integer(), nullable=False),
        sa.Column("scoring_version", sa.String(length=50), nullable=False),
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
        op.f("ix_report_evaluations_report_id"),
        "report_evaluations",
        ["report_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_report_evaluations_report_id"), table_name="report_evaluations")
    op.drop_table("report_evaluations")
