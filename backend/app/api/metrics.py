from datetime import date
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.ingestion.sec_client import SECClient, SECClientConfigurationError
from app.ingestion.xbrl import parse_company_facts
from app.models.company import Company
from app.models.filing import Filing
from app.models.financial_metric import FinancialMetric
from app.schemas.metric import FinancialMetricRead, FinancialMetricSyncResult

router = APIRouter(prefix="/companies/{ticker}/metrics", tags=["financial metrics"])
METRIC_UPSERT_BATCH_SIZE = 1_000


async def _company_or_404(ticker: str, db: AsyncSession) -> Company:
    result = await db.execute(select(Company).where(Company.ticker == ticker.upper()))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail=f"Company {ticker.upper()} was not found")
    return company


@router.get("", response_model=list[FinancialMetricRead])
async def list_financial_metrics(
    ticker: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    metric_name: str | None = None,
    as_of_date: date | None = None,
    fiscal_period: str | None = None,
    limit: Annotated[int, Query(ge=1, le=1_000)] = 100,
) -> list[FinancialMetric]:
    company = await _company_or_404(ticker, db)
    statement = select(FinancialMetric).where(FinancialMetric.company_id == company.id)
    if metric_name is not None:
        statement = statement.where(FinancialMetric.metric_name == metric_name)
    if as_of_date is not None:
        statement = statement.where(FinancialMetric.filing_date <= as_of_date)
    if fiscal_period is not None:
        statement = statement.where(FinancialMetric.fiscal_period == fiscal_period.upper())

    result = await db.execute(
        statement.order_by(
            FinancialMetric.period_end.desc(),
            FinancialMetric.filing_date.desc(),
            FinancialMetric.id.desc(),
        ).limit(limit)
    )
    return list(result.scalars().all())


@router.post("/sync", response_model=FinancialMetricSyncResult)
async def sync_financial_metrics(
    ticker: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> FinancialMetricSyncResult:
    company = await _company_or_404(ticker, db)
    try:
        async with SECClient() as client:
            payload = await client.get_company_facts(company.sec_cik)
    except SECClientConfigurationError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail=f"SEC returned HTTP {error.response.status_code}",
        ) from error
    except httpx.RequestError as error:
        raise HTTPException(status_code=502, detail="Could not reach the SEC API") from error

    facts = parse_company_facts(payload)
    filing_result = await db.execute(
        select(Filing.accession_number, Filing.id).where(Filing.company_id == company.id)
    )
    filing_ids = dict(filing_result.all())
    rows = [
        {
            "company_id": company.id,
            "filing_id": filing_ids.get(fact.accession_number),
            "metric_name": fact.metric_name,
            "taxonomy": fact.taxonomy,
            "xbrl_tag": fact.xbrl_tag,
            "value": fact.value,
            "unit": fact.unit,
            "period_start": fact.period_start,
            "period_end": fact.period_end,
            "filing_date": fact.filing_date,
            "fiscal_year": fact.fiscal_year,
            "fiscal_period": fact.fiscal_period,
            "form": fact.form,
            "accession_number": fact.accession_number,
            "frame": fact.frame,
        }
        for fact in facts
    ]

    for start in range(0, len(rows), METRIC_UPSERT_BATCH_SIZE):
        statement = insert(FinancialMetric).values(
            rows[start : start + METRIC_UPSERT_BATCH_SIZE]
        )
        excluded = statement.excluded
        await db.execute(
            statement.on_conflict_do_update(
                constraint="uq_financial_metrics_fact",
                set_={
                    "filing_id": excluded.filing_id,
                    "metric_name": excluded.metric_name,
                    "value": excluded.value,
                    "filing_date": excluded.filing_date,
                    "fiscal_year": excluded.fiscal_year,
                    "fiscal_period": excluded.fiscal_period,
                    "form": excluded.form,
                    "frame": excluded.frame,
                },
            )
        )

    if rows:
        await db.commit()

    return FinancialMetricSyncResult(
        ticker=company.ticker,
        fetched=len(facts),
        upserted=len(rows),
    )
