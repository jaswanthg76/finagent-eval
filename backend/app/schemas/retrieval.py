from datetime import date, datetime

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

