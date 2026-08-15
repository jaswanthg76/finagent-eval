import asyncio
from datetime import UTC, datetime
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.ingestion.filing_parser import chunk_text, parse_filing_html
from app.ingestion.sec_client import (
    SECClient,
    SECClientConfigurationError,
    SECDocumentError,
)
from app.models.filing import Filing
from app.models.filing_chunk import FilingChunk
from app.models.filing_section import FilingSection
from app.schemas.filing import FilingIngestResult, FilingSectionRead

router = APIRouter(prefix="/filings", tags=["filings"])


async def _filing_or_404(filing_id: int, db: AsyncSession) -> Filing:
    filing = await db.get(Filing, filing_id)
    if filing is None:
        raise HTTPException(status_code=404, detail=f"Filing {filing_id} was not found")
    return filing


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


@router.post("/{filing_id}/ingest", response_model=FilingIngestResult)
async def ingest_filing(
    filing_id: int, db: Annotated[AsyncSession, Depends(get_db)]
) -> FilingIngestResult:
    filing = await _filing_or_404(filing_id, db)
    try:
        async with SECClient() as client:
            html = await client.get_filing_document(filing.document_url)
    except SECClientConfigurationError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except SECDocumentError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except httpx.HTTPStatusError as error:
        raise HTTPException(
            status_code=502,
            detail=f"SEC returned HTTP {error.response.status_code}",
        ) from error
    except httpx.RequestError as error:
        raise HTTPException(status_code=502, detail="Could not download the SEC filing") from error

    parsed_sections = await asyncio.to_thread(parse_filing_html, html, filing.form)
    if not parsed_sections:
        raise HTTPException(status_code=422, detail="The filing did not contain readable text")

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
                    token_count=(len(content) + 3) // 4,
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
