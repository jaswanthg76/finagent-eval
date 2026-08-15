"""Create and seed companies.

Revision ID: 20260815_0001
Revises:
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    companies = op.create_table(
        "companies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ticker", sa.String(length=10), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sec_cik", sa.String(length=10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sec_cik"),
    )
    op.create_index(op.f("ix_companies_ticker"), "companies", ["ticker"], unique=True)

    op.bulk_insert(
        companies,
        [
            {"ticker": "AMD", "name": "Advanced Micro Devices, Inc.", "sec_cik": "0000002488"},
            {"ticker": "GOOGL", "name": "Alphabet Inc.", "sec_cik": "0001652044"},
            {"ticker": "META", "name": "Meta Platforms, Inc.", "sec_cik": "0001326801"},
            {"ticker": "MSFT", "name": "Microsoft Corporation", "sec_cik": "0000789019"},
            {"ticker": "NVDA", "name": "NVIDIA Corporation", "sec_cik": "0001045810"},
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_companies_ticker"), table_name="companies")
    op.drop_table("companies")
