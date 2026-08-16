from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReportEvaluation(Base):
    __tablename__ = "report_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(
        ForeignKey("research_reports.id", ondelete="CASCADE"), unique=True, index=True
    )
    overall_score: Mapped[float] = mapped_column(Numeric(5, 2))
    grounding_score: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    numeric_accuracy_score: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    citation_score: Mapped[float] = mapped_column(Numeric(5, 2))
    temporal_integrity_score: Mapped[float | None] = mapped_column(
        Numeric(5, 2), nullable=True
    )
    total_claim_count: Mapped[int] = mapped_column(Integer)
    evaluated_claim_count: Mapped[int] = mapped_column(Integer)
    verified_claim_count: Mapped[int] = mapped_column(Integer)
    partially_supported_claim_count: Mapped[int] = mapped_column(Integer)
    unsupported_claim_count: Mapped[int] = mapped_column(Integer)
    contradiction_count: Mapped[int] = mapped_column(Integer)
    error_count: Mapped[int] = mapped_column(Integer)
    scoring_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
