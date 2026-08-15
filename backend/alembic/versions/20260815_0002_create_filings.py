"""Create filings.

Revision ID: 20260815_0002
Revises: 20260815_0001
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260815_0002"
down_revision: str | None = "20260815_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "filings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("accession_number", sa.String(length=20), nullable=False),
        sa.Column("form", sa.String(length=20), nullable=False),
        sa.Column("filing_date", sa.Date(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("primary_document", sa.String(length=255), nullable=False),
        sa.Column("document_url", sa.String(length=500), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("accession_number"),
    )
    op.create_index(op.f("ix_filings_company_id"), "filings", ["company_id"], unique=False)
    op.create_index(op.f("ix_filings_filing_date"), "filings", ["filing_date"], unique=False)
    op.create_index(op.f("ix_filings_form"), "filings", ["form"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_filings_form"), table_name="filings")
    op.drop_index(op.f("ix_filings_filing_date"), table_name="filings")
    op.drop_index(op.f("ix_filings_company_id"), table_name="filings")
    op.drop_table("filings")
