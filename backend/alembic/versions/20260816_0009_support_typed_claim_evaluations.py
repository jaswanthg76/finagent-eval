"""Support typed claim evaluations.

Revision ID: 20260816_0009
Revises: 20260816_0008
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260816_0009"
down_revision: str | None = "20260816_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_claim_evaluations_claim_id"), table_name="claim_evaluations")
    op.add_column(
        "claim_evaluations",
        sa.Column(
            "evidence_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_claim_evaluations_claim_id"),
        "claim_evaluations",
        ["claim_id"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_claim_evaluations_claim_id_evaluation_type",
        "claim_evaluations",
        ["claim_id", "evaluation_type"],
    )


def downgrade() -> None:
    op.execute("DELETE FROM claim_evaluations WHERE evaluation_type <> 'NUMERIC'")
    op.drop_constraint(
        "uq_claim_evaluations_claim_id_evaluation_type",
        "claim_evaluations",
        type_="unique",
    )
    op.drop_index(op.f("ix_claim_evaluations_claim_id"), table_name="claim_evaluations")
    op.drop_column("claim_evaluations", "evidence_ids")
    op.create_index(
        op.f("ix_claim_evaluations_claim_id"),
        "claim_evaluations",
        ["claim_id"],
        unique=True,
    )
