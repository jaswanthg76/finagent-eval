from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.ingestion.sec_client import SECClient, SECClientConfigurationError
from app.models.company import Company
from app.models.filing import Filing
from app.schemas.company import CompanyRead
from app.schemas.filing import FilingRead, FilingSyncResult

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=list[CompanyRead])
async def list_companies(db: Annotated[AsyncSession, Depends(get_db)]) -> list[Company]:
    result = await db.execute(select(Company).order_by(Company.ticker))
    return list(result.scalars().all())


async def _company_or_404(ticker: str, db: AsyncSession) -> Company:
    result = await db.execute(select(Company).where(Company.ticker == ticker.upper()))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail=f"Company {ticker.upper()} was not found")
    return company


@router.get("/{ticker}/filings", response_model=list[FilingRead])
async def list_filings(ticker: str, db: Annotated[AsyncSession, Depends(get_db)]) -> list[Filing]:
    company = await _company_or_404(ticker, db)
    result = await db.execute(
        select(Filing)
        .where(Filing.company_id == company.id)
        .order_by(Filing.filing_date.desc(), Filing.accession_number.desc())
    )
    return list(result.scalars().all())


@router.post("/{ticker}/filings/sync", response_model=FilingSyncResult)
async def sync_filings(
    ticker: str, db: Annotated[AsyncSession, Depends(get_db)]
) -> FilingSyncResult:
    company = await _company_or_404(ticker, db)
    try:
        async with SECClient() as client:
            filing_metadata = await client.get_recent_filings(company.sec_cik)
    except SECClientConfigurationError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail=f"SEC returned HTTP {error.response.status_code}",
        ) from error
    except httpx.RequestError as error:
        raise HTTPException(status_code=502, detail="Could not reach the SEC API") from error

    result = await db.execute(
        select(Filing.accession_number).where(Filing.company_id == company.id)
    )
    existing_accessions = set(result.scalars().all())
    new_filings = [
        Filing(company_id=company.id, **vars(item))
        for item in filing_metadata
        if item.accession_number not in existing_accessions
    ]
    db.add_all(new_filings)
    await db.commit()

    return FilingSyncResult(
        ticker=company.ticker,
        fetched=len(filing_metadata),
        created=len(new_filings),
    )
