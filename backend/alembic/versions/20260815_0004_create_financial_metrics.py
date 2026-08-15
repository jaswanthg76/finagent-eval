"""Create financial metrics.

Revision ID: 20260815_0004
Revises: 20260815_0003
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0004"
down_revision: str | None = "20260815_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financial_metrics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=True),
        sa.Column("metric_name", sa.String(length=100), nullable=False),
        sa.Column("taxonomy", sa.String(length=50), nullable=False),
        sa.Column("xbrl_tag", sa.String(length=255), nullable=False),
        sa.Column("value", sa.Numeric(precision=38, scale=6), nullable=False),
        sa.Column("unit", sa.String(length=50), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=True),
        sa.Column("fiscal_period", sa.String(length=10), nullable=True),
        sa.Column("form", sa.String(length=20), nullable=False),
        sa.Column("accession_number", sa.String(length=20), nullable=False),
        sa.Column("frame", sa.String(length=32), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["filing_id"], ["filings.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id",
            "taxonomy",
            "xbrl_tag",
            "unit",
            "period_start",
            "period_end",
            "accession_number",
            name="uq_financial_metrics_fact",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_index(
        op.f("ix_financial_metrics_accession_number"),
        "financial_metrics",
        ["accession_number"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_metrics_company_id"),
        "financial_metrics",
        ["company_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_metrics_filing_date"),
        "financial_metrics",
        ["filing_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_metrics_filing_id"),
        "financial_metrics",
        ["filing_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_metrics_metric_name"),
        "financial_metrics",
        ["metric_name"],
        unique=False,
    )
    op.create_index(
        op.f("ix_financial_metrics_period_end"),
        "financial_metrics",
        ["period_end"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_financial_metrics_period_end"), table_name="financial_metrics")
    op.drop_index(op.f("ix_financial_metrics_metric_name"), table_name="financial_metrics")
    op.drop_index(op.f("ix_financial_metrics_filing_id"), table_name="financial_metrics")
    op.drop_index(op.f("ix_financial_metrics_filing_date"), table_name="financial_metrics")
    op.drop_index(op.f("ix_financial_metrics_company_id"), table_name="financial_metrics")
    op.drop_index(op.f("ix_financial_metrics_accession_number"), table_name="financial_metrics")
    op.drop_table("financial_metrics")

