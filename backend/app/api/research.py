from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.retrieval import _semantic_search_for_company
from app.core.config import settings
from app.core.database import get_db
from app.models.company import Company
from app.models.filing import Filing
from app.models.financial_metric import FinancialMetric
from app.models.research_report import ResearchReport
from app.research.agent import (
    AgentConfigurationError,
    AgentGenerationError,
    GroqResearchAgent,
)
from app.research.hybrid import identify_metric_names
from app.schemas.research import (
    ResearchAnswer,
    ResearchReportRead,
    ResearchReportSummary,
    ResearchRequest,
    ResearchSource,
    ResearchToolCall,
)
from app.schemas.retrieval import MetricEvidence, SemanticSearchResult

router = APIRouter(prefix="/research", tags=["research agent"])


def _stored_report_read(report: ResearchReport, ticker: str) -> ResearchReportRead:
    return ResearchReportRead(
        id=report.id,
        ticker=ticker,
        question=report.question,
        as_of_date=report.as_of_date,
        form=report.form,
        answer=report.answer,
        provider=report.provider,
        model=report.model,
        tool_calls=report.tool_calls,
        sources=report.sources,
        metrics=report.metrics,
        chunks=report.chunks,
        created_at=report.created_at,
    )


async def _company_or_404(ticker: str, db: AsyncSession) -> Company:
    result = await db.execute(select(Company).where(Company.ticker == ticker.upper()))
    company = result.scalar_one_or_none()
    if company is None:
        raise HTTPException(status_code=404, detail=f"Company {ticker.upper()} was not found")
    return company


async def _metric_evidence(
    *,
    company: Company,
    metric_names: list[str],
    limit_per_metric: int,
    as_of_date: date | None,
    form: str | None,
    db: AsyncSession,
) -> list[MetricEvidence]:
    statement = (
        select(FinancialMetric, Filing.document_url)
        .outerjoin(Filing, Filing.id == FinancialMetric.filing_id)
        .where(
            FinancialMetric.company_id == company.id,
            FinancialMetric.metric_name.in_(metric_names),
        )
    )
    if as_of_date is not None:
        statement = statement.where(FinancialMetric.filing_date <= as_of_date)
    if form is not None:
        statement = statement.where(FinancialMetric.form == form)

    result = await db.execute(
        statement.order_by(
            FinancialMetric.period_end.desc(),
            FinancialMetric.filing_date.desc(),
            FinancialMetric.id.desc(),
        )
    )
    seen: set[tuple[str, date | None, date, str]] = set()
    grouped: dict[str, list[tuple[FinancialMetric, str | None]]] = {
        name: [] for name in metric_names
    }
    for metric, source_url in result.all():
        identity = (metric.metric_name, metric.period_start, metric.period_end, metric.unit)
        if identity in seen:
            continue
        seen.add(identity)
        grouped[metric.metric_name].append((metric, source_url))

    evidence: list[MetricEvidence] = []
    for metric_name in metric_names:
        candidates = grouped[metric_name]
        if not candidates:
            continue
        latest = candidates[0][0]
        latest_duration = (
            (latest.period_end - latest.period_start).days if latest.period_start else None
        )

        def is_comparable(
            item: tuple[FinancialMetric, str | None],
            target_period: str | None = latest.fiscal_period,
            target_duration: int | None = latest_duration,
        ) -> bool:
            metric = item[0]
            duration = (metric.period_end - metric.period_start).days if metric.period_start else None
            duration_matches = (
                duration is None and target_duration is None
                or duration is not None
                and target_duration is not None
                and abs(duration - target_duration) <= 15
            )
            return metric.fiscal_period == target_period and duration_matches

        comparable = [item for item in candidates[1:] if is_comparable(item)]
        remaining = [item for item in candidates[1:] if item not in comparable]
        for metric, source_url in ([candidates[0]] + comparable + remaining)[:limit_per_metric]:
            evidence.append(
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
    return evidence


@router.post("", response_model=ResearchReportRead)
async def generate_research_answer(
    request: ResearchRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResearchReportRead:
    company = await _company_or_404(request.ticker, db)
    chunks: list[SemanticSearchResult] = []
    metrics: list[MetricEvidence] = []
    chunk_evidence_ids: dict[int, str] = {}
    metric_evidence_ids: dict[int, str] = {}
    prefer_recent_filings = any(
        term in request.question.lower()
        for term in ("latest", "most recent", "currently", "current period", "recent")
    )
    prefer_mda = any(
        term in request.question.lower()
        for term in ("drove", "driver", "why", "cause", "explain", "management said")
    )

    async def execute_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
        if name == "search_filings":
            filed_after: date | None = None
            if prefer_recent_filings:
                filing_dates = select(Filing.filing_date).where(Filing.company_id == company.id)
                if request.form is not None:
                    filing_dates = filing_dates.where(Filing.form == request.form)
                if request.as_of_date is not None:
                    filing_dates = filing_dates.where(Filing.filing_date <= request.as_of_date)
                date_result = await db.execute(
                    filing_dates.distinct().order_by(Filing.filing_date.desc()).limit(4)
                )
                recent_dates = list(date_result.scalars().all())
                filed_after = min(recent_dates) if recent_dates else None
            results = await _semantic_search_for_company(
                company=company,
                query=str(arguments["query"]),
                db=db,
                as_of_date=request.as_of_date,
                form=request.form,
                limit=int(arguments["limit"]),
                filed_after=filed_after,
                section_name="Management's Discussion and Analysis" if prefer_mda else None,
            )
            items: list[dict[str, object]] = []
            for result in results:
                evidence_id = chunk_evidence_ids.get(result.chunk_id)
                if evidence_id is None:
                    chunks.append(result)
                    evidence_id = f"F{len(chunks)}"
                    chunk_evidence_ids[result.chunk_id] = evidence_id
                model_evidence = result.model_dump(mode="json")
                model_evidence["content"] = result.content[:3_500]
                items.append({"evidence_id": evidence_id, **model_evidence})
            return {"filing_evidence": items}

        if name == "get_financial_metrics":
            results = await _metric_evidence(
                company=company,
                metric_names=[str(value) for value in arguments["metric_names"]],
                limit_per_metric=int(arguments["limit_per_metric"]),
                as_of_date=request.as_of_date,
                form=request.form,
                db=db,
            )
            items = []
            for result in results:
                evidence_id = metric_evidence_ids.get(result.metric_id)
                if evidence_id is None:
                    metrics.append(result)
                    evidence_id = f"M{len(metrics)}"
                    metric_evidence_ids[result.metric_id] = evidence_id
                items.append(
                    {
                        "evidence_id": evidence_id,
                        "metric_name": result.metric_name,
                        "value": str(result.value),
                        "unit": result.unit,
                        "period_start": result.period_start.isoformat()
                        if result.period_start
                        else None,
                        "period_end": result.period_end.isoformat(),
                        "filing_date": result.filing_date.isoformat(),
                        "form": result.form,
                        "source_url": result.source_url,
                    }
                )

            comparisons: list[dict[str, object]] = []
            by_name: dict[str, list[tuple[str, MetricEvidence]]] = {}
            for item, result in zip(items, results, strict=True):
                by_name.setdefault(result.metric_name, []).append(
                    (str(item["evidence_id"]), result)
                )
            for metric_name, facts in by_name.items():
                if len(facts) < 2:
                    continue
                newer_id, newer = facts[0]
                for older_id, older in facts[1:]:
                    if newer.unit != older.unit or older.value == 0:
                        continue
                    absolute_change = newer.value - older.value
                    percent_change = (absolute_change / older.value * Decimal(100)).quantize(
                        Decimal("0.1")
                    )
                    comparisons.append(
                        {
                            "metric_name": metric_name,
                            "newer_evidence_id": newer_id,
                            "older_evidence_id": older_id,
                            "absolute_change": str(absolute_change),
                            "percent_change": str(percent_change),
                            "unit": newer.unit,
                        }
                    )
            return {
                "metric_evidence": items,
                "deterministic_comparisons": comparisons,
            }

        raise ValueError(f"Unknown research tool: {name}")

    try:
        answer, raw_tool_calls = await GroqResearchAgent().answer(
            ticker=company.ticker,
            question=request.question,
            as_of_date=request.as_of_date.isoformat() if request.as_of_date else None,
            form=request.form,
            required_metric_names=identify_metric_names(request.question),
            execute_tool=execute_tool,
        )
    except AgentConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except AgentGenerationError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    sources = [
        ResearchSource(
            evidence_id=f"M{index}",
            kind="metric",
            title=f"{metric.metric_name} · {metric.period_end}",
            source_url=metric.source_url,
        )
        for index, metric in enumerate(metrics, start=1)
    ] + [
        ResearchSource(
            evidence_id=f"F{index}",
            kind="filing",
            title=f"{chunk.form} · {chunk.section_name} · filed {chunk.filing_date}",
            source_url=chunk.source_url,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]
    generated_answer = ResearchAnswer(
        ticker=company.ticker,
        question=request.question,
        as_of_date=request.as_of_date,
        form=request.form,
        answer=answer,
        provider=settings.ai_provider,
        model=settings.ai_model,
        tool_calls=[ResearchToolCall.model_validate(call) for call in raw_tool_calls],
        sources=sources,
        metrics=metrics,
        chunks=chunks,
    )
    report = ResearchReport(
        company_id=company.id,
        question=generated_answer.question,
        as_of_date=generated_answer.as_of_date,
        form=generated_answer.form,
        answer=generated_answer.answer,
        provider=generated_answer.provider,
        model=generated_answer.model,
        tool_calls=[call.model_dump(mode="json") for call in generated_answer.tool_calls],
        sources=[source.model_dump(mode="json") for source in generated_answer.sources],
        metrics=[metric.model_dump(mode="json") for metric in generated_answer.metrics],
        chunks=[chunk.model_dump(mode="json") for chunk in generated_answer.chunks],
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return _stored_report_read(report, company.ticker)


@router.get("/reports", response_model=list[ResearchReportSummary])
async def list_research_reports(
    db: Annotated[AsyncSession, Depends(get_db)],
    ticker: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ResearchReportSummary]:
    statement = select(ResearchReport, Company.ticker).join(
        Company, Company.id == ResearchReport.company_id
    )
    if ticker is not None:
        statement = statement.where(Company.ticker == ticker.upper())
    result = await db.execute(statement.order_by(ResearchReport.created_at.desc()).limit(limit))
    return [
        ResearchReportSummary(
            id=report.id,
            ticker=company_ticker,
            question=report.question,
            as_of_date=report.as_of_date,
            form=report.form,
            answer=report.answer,
            provider=report.provider,
            model=report.model,
            source_count=len(report.sources),
            created_at=report.created_at,
        )
        for report, company_ticker in result.all()
    ]


@router.get("/reports/{report_id}", response_model=ResearchReportRead)
async def get_research_report(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ResearchReportRead:
    result = await db.execute(
        select(ResearchReport, Company.ticker)
        .join(Company, Company.id == ResearchReport.company_id)
        .where(ResearchReport.id == report_id)
    )
    row = result.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    report, ticker = row
    return _stored_report_read(report, ticker)
