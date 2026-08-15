"""Create claim evaluations.

Revision ID: 20260816_0008
Revises: 20260816_0007
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260816_0008"
down_revision: str | None = "20260816_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "claim_evaluations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("evaluation_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("claimed_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("calculated_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verifier_version", sa.String(length=50), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["claim_id"], ["research_claims.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_claim_evaluations_claim_id"),
        "claim_evaluations",
        ["claim_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_claim_evaluations_status"),
        "claim_evaluations",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_claim_evaluations_status"), table_name="claim_evaluations")
    op.drop_index(op.f("ix_claim_evaluations_claim_id"), table_name="claim_evaluations")
    op.drop_table("claim_evaluations")
