from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class FinancialMetric(Base):
    __tablename__ = "financial_metrics"
    __table_args__ = (
        UniqueConstraint(
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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    filing_id: Mapped[int | None] = mapped_column(
        ForeignKey("filings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    metric_name: Mapped[str] = mapped_column(String(100), index=True)
    taxonomy: Mapped[str] = mapped_column(String(50))
    xbrl_tag: Mapped[str] = mapped_column(String(255))
    value: Mapped[Decimal] = mapped_column(Numeric(38, 6))
    unit: Mapped[str] = mapped_column(String(50))
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    filing_date: Mapped[date] = mapped_column(Date, index=True)
    fiscal_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fiscal_period: Mapped[str | None] = mapped_column(String(10), nullable=True)
    form: Mapped[str] = mapped_column(String(20))
    accession_number: Mapped[str] = mapped_column(String(20), index=True)
    frame: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

