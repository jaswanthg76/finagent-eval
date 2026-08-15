from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class FilingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    document_url: str
    ingested_at: datetime | None
    created_at: datetime


class FilingSyncResult(BaseModel):
    ticker: str
    fetched: int
    created: int


class FilingSectionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filing_id: int
    section_name: str
    section_order: int
    content: str
    created_at: datetime


class FilingIngestResult(BaseModel):
    filing_id: int
    sections_created: int
    chunks_created: int
    ingested_at: datetime


class FilingReingestFailure(BaseModel):
    filing_id: int
    detail: str


class FilingsReingestResult(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[FilingIngestResult]
    failures: list[FilingReingestFailure]
