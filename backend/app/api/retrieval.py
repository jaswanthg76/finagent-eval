from datetime import UTC, date, datetime
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from openai import APIError, RateLimitError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.company import Company
from app.models.filing import Filing
from app.models.filing_chunk import FilingChunk
from app.models.filing_section import FilingSection
from app.retrieval.embeddings import (
    EmbeddingConfigurationError,
    OpenAIEmbeddingClient,
    content_hash,
)
from app.schemas.retrieval import ChunkEmbeddingSyncResult, SemanticSearchResult

router = APIRouter(prefix="/companies/{ticker}", tags=["retrieval"])


async def _company_or_404(ticker: str, db: AsyncSession) -> Company:
    result = await db.execute(select(Company).where(Company.ticker == ticker.upper()))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail=f"Company {ticker.upper()} was not found")
    return company


def _raise_embedding_error(error: Exception) -> NoReturn:
    if isinstance(error, EmbeddingConfigurationError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    if isinstance(error, RateLimitError):
        raise HTTPException(status_code=429, detail="OpenAI embedding rate limit exceeded") from error
    raise HTTPException(status_code=502, detail="OpenAI embedding request failed") from error


@router.post("/embeddings/sync", response_model=ChunkEmbeddingSyncResult)
async def sync_chunk_embeddings(
    ticker: str,
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
    limit: Annotated[int, Query(ge=1, le=5_000)] = 500,
) -> ChunkEmbeddingSyncResult:
    company = await _company_or_404(ticker, db)
    result = await db.execute(
        select(FilingChunk)
        .join(FilingSection, FilingSection.id == FilingChunk.section_id)
        .join(Filing, Filing.id == FilingSection.filing_id)
        .where(Filing.company_id == company.id)
        .order_by(FilingChunk.id)
    )
    chunks = list(result.scalars().all())
    eligible = [
        chunk
        for chunk in chunks
        if force
        or chunk.embedding is None
        or chunk.embedding_model != settings.embedding_model
        or chunk.embedding_content_hash != content_hash(chunk.content)
    ]
    selected = eligible[:limit]

    if selected:
        try:
            client = OpenAIEmbeddingClient()
            for start in range(0, len(selected), settings.embedding_batch_size):
                batch = selected[start : start + settings.embedding_batch_size]
                vectors = await client.embed_texts([chunk.content for chunk in batch])
                embedded_at = datetime.now(UTC)
                for chunk, vector in zip(batch, vectors, strict=True):
                    chunk.embedding = vector
                    chunk.embedding_model = settings.embedding_model
                    chunk.embedding_content_hash = content_hash(chunk.content)
                    chunk.embedded_at = embedded_at
            await db.commit()
        except (EmbeddingConfigurationError, APIError) as error:
            await db.rollback()
            _raise_embedding_error(error)

    return ChunkEmbeddingSyncResult(
        ticker=company.ticker,
        total_chunks=len(chunks),
        eligible=len(eligible),
        embedded=len(selected),
        skipped=len(chunks) - len(eligible),
        remaining=len(eligible) - len(selected),
        model=settings.embedding_model,
        dimensions=settings.embedding_dimensions,
    )


@router.get("/search", response_model=list[SemanticSearchResult])
async def semantic_search(
    ticker: str,
    query: Annotated[str, Query(min_length=2, max_length=2_000)],
    db: Annotated[AsyncSession, Depends(get_db)],
    as_of_date: date | None = None,
    form: str | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 10,
) -> list[SemanticSearchResult]:
    company = await _company_or_404(ticker, db)
    available_statement = (
        select(func.count())
        .select_from(FilingChunk)
        .join(FilingSection, FilingSection.id == FilingChunk.section_id)
        .join(Filing, Filing.id == FilingSection.filing_id)
        .where(
            Filing.company_id == company.id,
            FilingChunk.embedding.is_not(None),
            FilingChunk.embedding_model == settings.embedding_model,
        )
    )
    if not await db.scalar(available_statement):
        raise HTTPException(
            status_code=409,
            detail=f"No {settings.embedding_model} embeddings exist for {company.ticker}",
        )

    try:
        query_vector = (await OpenAIEmbeddingClient().embed_texts([query]))[0]
    except (EmbeddingConfigurationError, APIError) as error:
        _raise_embedding_error(error)

    distance = FilingChunk.embedding.cosine_distance(query_vector).label("distance")
    statement = (
        select(
            FilingChunk.id.label("chunk_id"),
            FilingChunk.chunk_index,
            FilingChunk.content,
            FilingChunk.token_count,
            FilingChunk.embedded_at,
            FilingSection.section_name,
            Filing.id.label("filing_id"),
            Filing.accession_number,
            Filing.form,
            Filing.filing_date,
            Filing.report_date,
            Filing.document_url.label("source_url"),
            distance,
        )
        .join(FilingSection, FilingSection.id == FilingChunk.section_id)
        .join(Filing, Filing.id == FilingSection.filing_id)
        .where(
            Filing.company_id == company.id,
            FilingChunk.embedding.is_not(None),
            FilingChunk.embedding_model == settings.embedding_model,
        )
    )
    if as_of_date is not None:
        statement = statement.where(Filing.filing_date <= as_of_date)
    if form is not None:
        statement = statement.where(Filing.form == form.upper())

    result = await db.execute(statement.order_by(distance).limit(limit))
    return [
        SemanticSearchResult(
            chunk_id=row.chunk_id,
            filing_id=row.filing_id,
            accession_number=row.accession_number,
            form=row.form,
            filing_date=row.filing_date,
            report_date=row.report_date,
            section_name=row.section_name,
            chunk_index=row.chunk_index,
            content=row.content,
            token_count=row.token_count,
            similarity=max(-1.0, min(1.0, 1.0 - float(row.distance))),
            source_url=row.source_url,
            embedded_at=row.embedded_at,
        )
        for row in result
    ]
