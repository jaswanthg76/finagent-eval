from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class FinancialMetricRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    filing_id: int | None
    metric_name: str
    taxonomy: str
    xbrl_tag: str
    value: Decimal
    unit: str
    period_start: date | None
    period_end: date
    filing_date: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession_number: str
    frame: str | None
    created_at: datetime


class FinancialMetricSyncResult(BaseModel):
    ticker: str
    fetched: int
    upserted: int

