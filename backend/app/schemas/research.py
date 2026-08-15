from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.retrieval import MetricEvidence, SemanticSearchResult


class ResearchRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    question: str = Field(min_length=2, max_length=2_000)
    as_of_date: date | None = None
    form: Literal["10-K", "10-Q", "8-K"] | None = None


class ResearchToolCall(BaseModel):
    name: str
    arguments: dict[str, object]


class ResearchSource(BaseModel):
    evidence_id: str
    kind: Literal["filing", "metric"]
    title: str
    source_url: str | None


class ResearchAnswer(BaseModel):
    ticker: str
    question: str
    as_of_date: date | None
    form: str | None
    answer: str
    provider: str
    model: str
    tool_calls: list[ResearchToolCall]
    sources: list[ResearchSource]
    metrics: list[MetricEvidence]
    chunks: list[SemanticSearchResult]


class ResearchReportRead(ResearchAnswer):
    id: int
    created_at: datetime


class ResearchReportSummary(BaseModel):
    id: int
    ticker: str
    question: str
    as_of_date: date | None
    form: str | None
    answer: str
    provider: str
    model: str
    source_count: int
    created_at: datetime
