import asyncio
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.ingestion.filing_parser import chunk_text, count_tokens, parse_filing_html
from app.ingestion.sec_client import (
    SECClient,
    SECClientConfigurationError,
    SECDocumentError,
)
from app.models.filing import Filing
from app.models.filing_chunk import FilingChunk
from app.models.filing_section import FilingSection
from app.schemas.filing import (
    FilingIngestResult,
    FilingReingestFailure,
    FilingSectionRead,
    FilingsReingestResult,
)

router = APIRouter(prefix="/filings", tags=["filings"])


async def _filing_or_404(filing_id: int, db: AsyncSession) -> Filing:
    filing = await db.get(Filing, filing_id)
    if filing is None:
        raise HTTPException(status_code=404, detail=f"Filing {filing_id} was not found")
    return filing


def _ingest_error_detail(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"SEC returned HTTP {error.response.status_code}"
    if isinstance(error, httpx.RequestError):
        return "Could not download the SEC filing"
    return str(error)


def _raise_ingest_http_error(error: Exception) -> None:
    status_code = 500 if isinstance(error, SECClientConfigurationError) else 422
    if isinstance(error, (httpx.HTTPStatusError, httpx.RequestError)):
        status_code = 502
    raise HTTPException(status_code=status_code, detail=_ingest_error_detail(error)) from error


async def _ingest_filing_content(
    filing: Filing, db: AsyncSession, client: SECClient
) -> FilingIngestResult:
    html = await client.get_filing_document(filing.document_url)
    parsed_sections = await asyncio.to_thread(parse_filing_html, html, filing.form)
    if not parsed_sections:
        raise SECDocumentError("The filing did not contain readable text")

    await db.execute(delete(FilingSection).where(FilingSection.filing_id == filing.id))
    sections_created = 0
    chunks_created = 0
    for section_order, parsed_section in enumerate(parsed_sections):
        section = FilingSection(
            filing_id=filing.id,
            section_name=parsed_section.name,
            section_order=section_order,
            content=parsed_section.content,
        )
        db.add(section)
        await db.flush()
        sections_created += 1

        for chunk_index, content in enumerate(chunk_text(parsed_section.content)):
            db.add(
                FilingChunk(
                    section_id=section.id,
                    chunk_index=chunk_index,
                    content=content,
                    token_count=count_tokens(content),
                )
            )
            chunks_created += 1

    filing.ingested_at = datetime.now(UTC)
    await db.commit()

    return FilingIngestResult(
        filing_id=filing.id,
        sections_created=sections_created,
        chunks_created=chunks_created,
        ingested_at=filing.ingested_at,
    )


@router.get("/{filing_id}/sections", response_model=list[FilingSectionRead])
async def list_filing_sections(
    filing_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> list[FilingSection]:
    await _filing_or_404(filing_id, db)
    result = await db.execute(
        select(FilingSection)
        .where(FilingSection.filing_id == filing_id)
        .order_by(FilingSection.section_order)
    )
    return list(result.scalars().all())


@router.post("/reingest-all", response_model=FilingsReingestResult)
async def reingest_all_filings(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> FilingsReingestResult:
    result = await db.execute(select(Filing).order_by(Filing.id))
    filings = list(result.scalars().all())
    ingested: list[FilingIngestResult] = []
    failures: list[FilingReingestFailure] = []

    if not filings:
        return FilingsReingestResult(
            total=0,
            succeeded=0,
            failed=0,
            results=[],
            failures=[],
        )

    try:
        async with SECClient() as client:
            for filing in filings:
                try:
                    ingested.append(await _ingest_filing_content(filing, db, client))
                except (SECDocumentError, httpx.HTTPError) as error:
                    failures.append(
                        FilingReingestFailure(
                            filing_id=filing.id,
                            detail=_ingest_error_detail(error),
                        )
                    )
    except SECClientConfigurationError as error:
        _raise_ingest_http_error(error)

    return FilingsReingestResult(
        total=len(filings),
        succeeded=len(ingested),
        failed=len(failures),
        results=ingested,
        failures=failures,
    )


@router.post("/{filing_id}/ingest", response_model=FilingIngestResult)
async def ingest_filing(
    filing_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> FilingIngestResult:
    filing = await _filing_or_404(filing_id, db)
    try:
        async with SECClient() as client:
            return await _ingest_filing_content(filing, db, client)
    except (SECClientConfigurationError, SECDocumentError, httpx.HTTPError) as error:
        _raise_ingest_http_error(error)
