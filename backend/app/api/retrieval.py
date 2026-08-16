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
from app.models.financial_metric import FinancialMetric
from app.research.hybrid import identify_metric_names
from app.retrieval.embeddings import (
    EmbeddingConfigurationError,
    EmbeddingGenerationError,
    content_hash,
    create_embedding_client,
)
from app.schemas.retrieval import (
    ChunkEmbeddingSyncResult,
    HybridResearchResult,
    MetricEvidence,
    SemanticSearchResult,
)

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
    if isinstance(error, EmbeddingGenerationError):
        raise HTTPException(status_code=503, detail=str(error)) from error
    raise HTTPException(status_code=502, detail="Embedding request failed") from error


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
            client = create_embedding_client()
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
        except (EmbeddingConfigurationError, EmbeddingGenerationError, APIError) as error:
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
    return await _semantic_search_for_company(
        company=company,
        query=query,
        db=db,
        as_of_date=as_of_date,
        form=form,
        limit=limit,
    )


async def _semantic_search_for_company(
    company: Company,
    query: str,
    db: AsyncSession,
    as_of_date: date | None,
    form: str | None,
    limit: int,
    filed_after: date | None = None,
    section_name: str | None = None,
    accession_numbers: set[str] | None = None,
) -> list[SemanticSearchResult]:
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
        query_vector = (await create_embedding_client().embed_texts([query]))[0]
    except (EmbeddingConfigurationError, EmbeddingGenerationError, APIError) as error:
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
    if filed_after is not None:
        statement = statement.where(Filing.filing_date >= filed_after)
    if form is not None:
        statement = statement.where(Filing.form == form.upper())
    if section_name is not None:
        statement = statement.where(FilingSection.section_name == section_name)
    if accession_numbers is not None:
        statement = statement.where(Filing.accession_number.in_(accession_numbers))

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


@router.get("/research", response_model=HybridResearchResult)
async def hybrid_research(
    ticker: str,
    query: Annotated[str, Query(min_length=2, max_length=2_000)],
    db: Annotated[AsyncSession, Depends(get_db)],
    as_of_date: date | None = None,
    form: str | None = None,
    chunk_limit: Annotated[int, Query(ge=1, le=50)] = 8,
    metric_limit: Annotated[int, Query(ge=1, le=10)] = 4,
) -> HybridResearchResult:
    company = await _company_or_404(ticker, db)
    chunks = await _semantic_search_for_company(
        company=company,
        query=query,
        db=db,
        as_of_date=as_of_date,
        form=form,
        limit=chunk_limit,
    )

    matched_metric_names = identify_metric_names(query)
    metrics: list[MetricEvidence] = []
    if matched_metric_names:
        statement = (
            select(FinancialMetric, Filing.document_url)
            .outerjoin(Filing, Filing.id == FinancialMetric.filing_id)
            .where(
                FinancialMetric.company_id == company.id,
                FinancialMetric.metric_name.in_(matched_metric_names),
            )
        )
        if as_of_date is not None:
            statement = statement.where(FinancialMetric.filing_date <= as_of_date)
        if form is not None:
            statement = statement.where(FinancialMetric.form == form.upper())

        metric_result = await db.execute(
            statement.order_by(
                FinancialMetric.period_end.desc(),
                FinancialMetric.filing_date.desc(),
                FinancialMetric.id.desc(),
            )
        )
        counts = {metric_name: 0 for metric_name in matched_metric_names}
        seen_periods: set[tuple[str, date | None, date, str]] = set()
        for metric, source_url in metric_result.all():
            identity = (
                metric.metric_name,
                metric.period_start,
                metric.period_end,
                metric.unit,
            )
            if identity in seen_periods or counts[metric.metric_name] >= metric_limit:
                continue
            seen_periods.add(identity)
            counts[metric.metric_name] += 1
            metrics.append(
                MetricEvidence(
                    metric_id=metric.id,
                    metric_name=metric.metric_name,
                    value=metric.value,
                    unit=metric.unit,
                    period_start=metric.period_start,
                    period_end=metric.period_end,
                    filing_date=metric.filing_date,
                    fiscal_year=metric.fiscal_year,
                    fiscal_period=metric.fiscal_period,
                    form=metric.form,
                    accession_number=metric.accession_number,
                    source_url=source_url,
                )
            )

    return HybridResearchResult(
        ticker=company.ticker,
        query=query,
        matched_metric_names=matched_metric_names,
        metrics=metrics,
        chunks=chunks,
    )
