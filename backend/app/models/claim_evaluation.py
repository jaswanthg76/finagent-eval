from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ClaimEvaluation(Base):
    __tablename__ = "claim_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("research_claims.id", ondelete="CASCADE"), unique=True, index=True
    )
    evaluation_type: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), index=True)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4))
    reason: Mapped[str] = mapped_column(Text)
    claimed_values: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    calculated_values: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    verifier_version: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
