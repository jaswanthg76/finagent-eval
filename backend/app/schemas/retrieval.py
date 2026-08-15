from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel


class ChunkEmbeddingSyncResult(BaseModel):
    ticker: str
    total_chunks: int
    eligible: int
    embedded: int
    skipped: int
    remaining: int
    model: str
    dimensions: int


class SemanticSearchResult(BaseModel):
    chunk_id: int
    filing_id: int
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None
    section_name: str
    chunk_index: int
    content: str
    token_count: int
    similarity: float
    source_url: str
    embedded_at: datetime


class MetricEvidence(BaseModel):
    metric_id: int
    metric_name: str
    value: Decimal
    unit: str
    period_start: date | None
    period_end: date
    filing_date: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession_number: str
    source_url: str | None


class HybridResearchResult(BaseModel):
    ticker: str
    query: str
    matched_metric_names: list[str]
    metrics: list[MetricEvidence]
    chunks: list[SemanticSearchResult]
