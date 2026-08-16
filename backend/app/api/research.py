from datetime import date
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.retrieval import _semantic_search_for_company
from app.core.config import settings
from app.core.database import get_db
from app.evaluation.citations import (
    CITATION_VERIFIER_VERSION,
    CitationClaimInput,
    CitationEvaluationConfigurationError,
    CitationEvaluationError,
    GroqCitationEvaluator,
)
from app.evaluation.contradiction import (
    CONTRADICTION_BATCH_SIZE,
    CONTRADICTION_VERIFIER_VERSION,
    ContradictionClaimInput,
    ContradictionEvaluationConfigurationError,
    ContradictionEvaluationError,
    GroqContradictionEvaluator,
    is_contradiction_eligible,
)
from app.evaluation.evidence import relevant_excerpt
from app.evaluation.numeric import (
    NUMERIC_VERIFIER_VERSION,
    is_numeric_claim,
    verify_numeric_claim,
)
from app.evaluation.scoring import (
    SCORING_VERSION,
    ScoringClaim,
    ScoringEvaluation,
    calculate_report_score,
)
from app.evaluation.temporal import TEMPORAL_VERIFIER_VERSION, verify_temporal_integrity
from app.models.claim_evaluation import ClaimEvaluation
from app.models.company import Company
from app.models.filing import Filing
from app.models.financial_metric import FinancialMetric
from app.models.report_evaluation import ReportEvaluation
from app.models.report_temporal_evaluation import ReportTemporalEvaluation
from app.models.research_claim import ResearchClaim
from app.models.research_report import ResearchReport
from app.research.agent import (
    AgentConfigurationError,
    AgentGenerationError,
    GroqResearchAgent,
)
from app.research.claims import (
    CLAIM_EXTRACTION_PROMPT_VERSION,
    ClaimExtractionConfigurationError,
    ClaimExtractionError,
    GroqClaimExtractor,
)
from app.research.evidence import resolve_metric_name
from app.schemas.research import (
    CitationVerificationResult,
    ClaimEvaluationRead,
    ClaimExtractionResult,
    ContradictionVerificationResult,
    NumericVerificationResult,
    ReportEvaluationRead,
    ResearchAnswer,
    ResearchClaimRead,
    ResearchReportRead,
    ResearchReportSummary,
    ResearchRequest,
    ResearchSource,
    ResearchToolCall,
    TemporalEvaluationRead,
)
from app.schemas.retrieval import MetricEvidence, SemanticSearchResult

router = APIRouter(prefix="/research", tags=["research agent"])
RESEARCH_FILING_EVIDENCE_CHAR_BUDGET = 12_000
RESEARCH_FILING_EXCERPT_MAX_CHARS = 1_600


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


def _claim_read(claim: ResearchClaim) -> ResearchClaimRead:
    return ResearchClaimRead(
        id=claim.id,
        report_id=claim.report_id,
        claim_index=claim.claim_index,
        claim_text=claim.claim_text,
        claim_type=claim.claim_type,
        citation_ids=claim.citation_ids,
        created_at=claim.created_at,
    )


def _evaluation_read(evaluation: ClaimEvaluation) -> ClaimEvaluationRead:
    return ClaimEvaluationRead(
        id=evaluation.id,
        claim_id=evaluation.claim_id,
        evaluation_type=evaluation.evaluation_type,
        status=evaluation.status,
        confidence=float(evaluation.confidence),
        reason=evaluation.reason,
        evidence_ids=evaluation.evidence_ids,
        evaluated_evidence=evaluation.evaluated_evidence,
        claimed_values=evaluation.claimed_values,
        calculated_values=evaluation.calculated_values,
        verifier_version=evaluation.verifier_version,
        created_at=evaluation.created_at,
    )


def _report_evaluation_read(evaluation: ReportEvaluation) -> ReportEvaluationRead:
    return ReportEvaluationRead(
        id=evaluation.id,
        report_id=evaluation.report_id,
        overall_score=float(evaluation.overall_score),
        grounding_score=(
            float(evaluation.grounding_score)
            if evaluation.grounding_score is not None
            else None
        ),
        numeric_accuracy_score=(
            float(evaluation.numeric_accuracy_score)
            if evaluation.numeric_accuracy_score is not None
            else None
        ),
        citation_score=float(evaluation.citation_score),
        temporal_integrity_score=(
            float(evaluation.temporal_integrity_score)
            if evaluation.temporal_integrity_score is not None
            else None
        ),
        total_claim_count=evaluation.total_claim_count,
        evaluated_claim_count=evaluation.evaluated_claim_count,
        verified_claim_count=evaluation.verified_claim_count,
        partially_supported_claim_count=evaluation.partially_supported_claim_count,
        unsupported_claim_count=evaluation.unsupported_claim_count,
        contradiction_count=evaluation.contradiction_count,
        error_count=evaluation.error_count,
        scoring_version=evaluation.scoring_version,
        created_at=evaluation.created_at,
    )


def _temporal_evaluation_read(
    evaluation: ReportTemporalEvaluation,
) -> TemporalEvaluationRead:
    return TemporalEvaluationRead(
        id=evaluation.id,
        report_id=evaluation.report_id,
        status=evaluation.status,
        score=float(evaluation.score) if evaluation.score is not None else None,
        checked_source_count=evaluation.checked_source_count,
        violations=evaluation.violations,
        reason=evaluation.reason,
        verifier_version=evaluation.verifier_version,
        created_at=evaluation.created_at,
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
    remaining_filing_evidence_chars = RESEARCH_FILING_EVIDENCE_CHAR_BUDGET

    async def search_filing_evidence(query: str, limit: int) -> list[dict[str, object]]:
        nonlocal remaining_filing_evidence_chars
        if remaining_filing_evidence_chars < 200:
            return []
        filed_after: date | None = None
        accession_numbers: set[str] | None = None
        if metrics:
            filed_after = max(metric.filing_date for metric in metrics)
            accession_numbers = {
                metric.accession_number
                for metric in metrics
                if metric.filing_date == filed_after
            }
        results = await _semantic_search_for_company(
            company=company,
            query=query,
            db=db,
            as_of_date=request.as_of_date,
            form=request.form,
            limit=limit,
            filed_after=filed_after,
            section_name=None,
            accession_numbers=accession_numbers,
        )
        items: list[dict[str, object]] = []
        for result in results:
            if remaining_filing_evidence_chars < 200:
                break
            excerpt = relevant_excerpt(
                result.content,
                query,
                min(RESEARCH_FILING_EXCERPT_MAX_CHARS, remaining_filing_evidence_chars),
            )
            if not excerpt:
                continue
            evidence_id = chunk_evidence_ids.get(result.chunk_id)
            if evidence_id is None:
                chunks.append(result)
                evidence_id = f"F{len(chunks)}"
                chunk_evidence_ids[result.chunk_id] = evidence_id
            model_evidence = result.model_dump(mode="json")
            model_evidence["content"] = excerpt
            remaining_filing_evidence_chars -= len(excerpt)
            items.append({"evidence_id": evidence_id, **model_evidence})
        return items

    def store_metric_evidence(
        results: list[MetricEvidence],
    ) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
        items: list[dict[str, object]] = []
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
        return items, comparisons

    async def execute_tool(name: str, arguments: dict[str, object]) -> dict[str, object]:
        if name == "search_filings":
            return {
                "filing_evidence": await search_filing_evidence(
                    str(arguments["query"]), int(arguments["limit"])
                ),
                "evidence_budget_exhausted": remaining_filing_evidence_chars < 200,
            }
        if name == "get_financial_evidence":
            requested_concepts = [str(value) for value in arguments["concepts"]]
            metric_by_concept = {
                concept: resolve_metric_name(concept) for concept in requested_concepts
            }
            structured_metrics = list(
                dict.fromkeys(
                    metric_name
                    for metric_name in metric_by_concept.values()
                    if metric_name is not None
                )
            )
            results = (
                await _metric_evidence(
                    company=company,
                    metric_names=structured_metrics,
                    limit_per_metric=int(arguments["limit_per_concept"]),
                    as_of_date=request.as_of_date,
                    form=request.form,
                    db=db,
                )
                if structured_metrics
                else []
            )
            structured_evidence, comparisons = store_metric_evidence(results)
            found_metrics = {result.metric_name for result in results}
            filing_concepts = [
                concept
                for concept, metric_name in metric_by_concept.items()
                if metric_name is None or metric_name not in found_metrics
            ]
            filing_evidence: list[dict[str, object]] = []
            for concept in filing_concepts:
                concept_items = await search_filing_evidence(
                    concept, int(arguments["limit_per_concept"])
                )
                filing_evidence.extend(
                    {"concept": concept, **item} for item in concept_items
                )
            return {
                "requested_concepts": requested_concepts,
                "structured_evidence": structured_evidence,
                "filing_evidence": filing_evidence,
                "deterministic_comparisons": comparisons,
                "evidence_budget_exhausted": remaining_filing_evidence_chars < 200,
            }
        raise ValueError(f"Unknown research tool: {name}")
    try:
        answer, raw_tool_calls = await GroqResearchAgent().answer(
            ticker=company.ticker,
            question=request.question,
            as_of_date=request.as_of_date.isoformat() if request.as_of_date else None,
            form=request.form,
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


@router.get("/reports/{report_id}/claims", response_model=list[ResearchClaimRead])
async def list_research_claims(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ResearchClaimRead]:
    if await db.scalar(select(ResearchReport.id).where(ResearchReport.id == report_id)) is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    result = await db.execute(
        select(ResearchClaim)
        .where(ResearchClaim.report_id == report_id)
        .order_by(ResearchClaim.claim_index)
    )
    return [_claim_read(claim) for claim in result.scalars().all()]


@router.post(
    "/reports/{report_id}/claims/extract",
    response_model=ClaimExtractionResult,
)
async def extract_research_claims(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
) -> ClaimExtractionResult:
    report = await db.scalar(select(ResearchReport).where(ResearchReport.id == report_id))
    if report is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")

    existing_result = await db.execute(
        select(ResearchClaim)
        .where(ResearchClaim.report_id == report_id)
        .order_by(ResearchClaim.claim_index)
    )
    existing = list(existing_result.scalars().all())
    if existing and not force:
        extraction_model = str(existing[0].extraction_metadata.get("model", settings.ai_model))
        return ClaimExtractionResult(
            report_id=report_id,
            extracted=len(existing),
            model=extraction_model,
            claims=[_claim_read(claim) for claim in existing],
        )

    available_evidence_ids = {
        str(source["evidence_id"]).upper()
        for source in report.sources
        if source.get("evidence_id")
    }
    try:
        extracted = await GroqClaimExtractor().extract(
            answer=report.answer,
            available_evidence_ids=available_evidence_ids,
        )
    except ClaimExtractionConfigurationError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ClaimExtractionError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error

    if existing:
        await db.execute(delete(ResearchClaim).where(ResearchClaim.report_id == report_id))
        await db.execute(delete(ReportEvaluation).where(ReportEvaluation.report_id == report_id))
    claims = [
        ResearchClaim(
            report_id=report_id,
            claim_index=index,
            claim_text=claim.claim_text,
            claim_type=claim.claim_type,
            citation_ids=claim.citation_ids,
            extraction_metadata={
                "provider": settings.ai_provider,
                "model": settings.ai_model,
                "prompt_version": CLAIM_EXTRACTION_PROMPT_VERSION,
            },
        )
        for index, claim in enumerate(extracted, start=1)
    ]
    db.add_all(claims)
    await db.commit()
    for claim in claims:
        await db.refresh(claim)
    return ClaimExtractionResult(
        report_id=report_id,
        extracted=len(claims),
        model=settings.ai_model,
        claims=[_claim_read(claim) for claim in claims],
    )


@router.get(
    "/reports/{report_id}/evaluations",
    response_model=list[ClaimEvaluationRead],
)
async def list_claim_evaluations(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> list[ClaimEvaluationRead]:
    if await db.scalar(select(ResearchReport.id).where(ResearchReport.id == report_id)) is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    result = await db.execute(
        select(ClaimEvaluation)
        .join(ResearchClaim, ResearchClaim.id == ClaimEvaluation.claim_id)
        .where(ResearchClaim.report_id == report_id)
        .order_by(ResearchClaim.claim_index, ClaimEvaluation.evaluation_type)
    )
    return [_evaluation_read(evaluation) for evaluation in result.scalars().all()]


@router.post(
    "/reports/{report_id}/verify-numeric",
    response_model=NumericVerificationResult,
)
async def verify_report_numeric_claims(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
) -> NumericVerificationResult:
    report = await db.scalar(select(ResearchReport).where(ResearchReport.id == report_id))
    if report is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    claims_result = await db.execute(
        select(ResearchClaim)
        .where(ResearchClaim.report_id == report_id)
        .order_by(ResearchClaim.claim_index)
    )
    claims = list(claims_result.scalars().all())
    eligible = [
        claim for claim in claims if is_numeric_claim(claim.claim_type, claim.claim_text)
    ]
    if not eligible:
        return NumericVerificationResult(
            report_id=report_id,
            eligible_claims=0,
            verified_claims=0,
            evaluations=[],
        )

    eligible_ids = [claim.id for claim in eligible]
    existing_result = await db.execute(
        select(ClaimEvaluation).where(
            ClaimEvaluation.claim_id.in_(eligible_ids),
            ClaimEvaluation.evaluation_type == "NUMERIC",
        )
    )
    existing = list(existing_result.scalars().all())
    if (
        len(existing) == len(eligible)
        and all(item.verifier_version == NUMERIC_VERIFIER_VERSION for item in existing)
        and not force
    ):
        ordered = sorted(existing, key=lambda evaluation: eligible_ids.index(evaluation.claim_id))
        return NumericVerificationResult(
            report_id=report_id,
            eligible_claims=len(eligible),
            verified_claims=sum(item.status == "VERIFIED" for item in ordered),
            evaluations=[_evaluation_read(item) for item in ordered],
        )

    metrics_by_evidence_id = {
        f"M{index}": metric for index, metric in enumerate(report.metrics, start=1)
    }
    filings_by_evidence_id = {
        f"F{index}": chunk for index, chunk in enumerate(report.chunks, start=1)
    }
    if existing:
        await db.execute(
            delete(ClaimEvaluation).where(
                ClaimEvaluation.claim_id.in_(eligible_ids),
                ClaimEvaluation.evaluation_type == "NUMERIC",
            )
        )
    await db.execute(delete(ReportEvaluation).where(ReportEvaluation.report_id == report_id))
    evaluations: list[ClaimEvaluation] = []
    for claim in eligible:
        outcome = verify_numeric_claim(
            claim_text=claim.claim_text,
            citation_ids=claim.citation_ids,
            metrics_by_evidence_id=metrics_by_evidence_id,
            filings_by_evidence_id=filings_by_evidence_id,
        )
        evaluations.append(
            ClaimEvaluation(
                claim_id=claim.id,
                evaluation_type="NUMERIC",
                status=outcome.status,
                confidence=outcome.confidence,
                reason=outcome.reason,
                evidence_ids=[
                    evidence_id
                    for evidence_id in claim.citation_ids
                    if evidence_id in metrics_by_evidence_id
                    or evidence_id in filings_by_evidence_id
                ],
                evaluated_evidence=[],
                claimed_values=outcome.claimed_values,
                calculated_values=outcome.calculated_values,
                verifier_version=NUMERIC_VERIFIER_VERSION,
            )
        )
    db.add_all(evaluations)
    await db.commit()
    for evaluation in evaluations:
        await db.refresh(evaluation)
    return NumericVerificationResult(
        report_id=report_id,
        eligible_claims=len(eligible),
        verified_claims=sum(item.status == "VERIFIED" for item in evaluations),
        evaluations=[_evaluation_read(item) for item in evaluations],
    )


@router.post(
    "/reports/{report_id}/verify-citations",
    response_model=CitationVerificationResult,
)
async def verify_report_citations(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
) -> CitationVerificationResult:
    report = await db.scalar(select(ResearchReport).where(ResearchReport.id == report_id))
    if report is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    claims_result = await db.execute(
        select(ResearchClaim)
        .where(ResearchClaim.report_id == report_id)
        .order_by(ResearchClaim.claim_index)
    )
    claims = list(claims_result.scalars().all())
    eligible = [claim for claim in claims if claim.claim_type != "NUMERIC"]
    if not eligible:
        return CitationVerificationResult(
            report_id=report_id,
            eligible_claims=0,
            verified_claims=0,
            evaluations=[],
        )

    eligible_ids = [claim.id for claim in eligible]
    existing_result = await db.execute(
        select(ClaimEvaluation).where(
            ClaimEvaluation.claim_id.in_(eligible_ids),
            ClaimEvaluation.evaluation_type == "CITATION",
        )
    )
    existing = list(existing_result.scalars().all())
    if len(existing) == len(eligible) and not force:
        ordered = sorted(existing, key=lambda evaluation: eligible_ids.index(evaluation.claim_id))
        return CitationVerificationResult(
            report_id=report_id,
            eligible_claims=len(eligible),
            verified_claims=sum(item.status == "VERIFIED" for item in ordered),
            evaluations=[_evaluation_read(item) for item in ordered],
        )

    chunks_by_evidence_id = {
        f"F{index}": chunk for index, chunk in enumerate(report.chunks, start=1)
    }
    model_inputs: list[CitationClaimInput] = []
    for claim in eligible:
        filing_ids = [
            evidence_id
            for evidence_id in claim.citation_ids
            if evidence_id.startswith("F") and evidence_id in chunks_by_evidence_id
        ]
        if filing_ids:
            model_inputs.append(
                CitationClaimInput(
                    claim_id=claim.id,
                    claim_text=claim.claim_text,
                    evidence_ids=filing_ids,
                )
            )

    model_results = []
    if model_inputs:
        try:
            evaluator = GroqCitationEvaluator()
            for claim_input in model_inputs:
                model_results.extend(
                    await evaluator.evaluate(
                        claims=[claim_input],
                        evidence_by_id={
                            evidence_id: chunks_by_evidence_id[evidence_id]
                            for evidence_id in claim_input.evidence_ids
                        },
                    )
                )
        except CitationEvaluationConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except CitationEvaluationError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
    result_by_claim_id = {result.claim_id: result for result in model_results}

    if existing:
        await db.execute(
            delete(ClaimEvaluation).where(
                ClaimEvaluation.claim_id.in_(eligible_ids),
                ClaimEvaluation.evaluation_type == "CITATION",
            )
        )
    await db.execute(delete(ReportEvaluation).where(ReportEvaluation.report_id == report_id))
    evaluations: list[ClaimEvaluation] = []
    for claim in eligible:
        filing_ids = [
            evidence_id
            for evidence_id in claim.citation_ids
            if evidence_id.startswith("F") and evidence_id in chunks_by_evidence_id
        ]
        result = result_by_claim_id.get(claim.id)
        evaluations.append(
            ClaimEvaluation(
                claim_id=claim.id,
                evaluation_type="CITATION",
                status=result.status if result else "UNSUPPORTED",
                confidence=result.confidence if result else 1.0,
                reason=(
                    result.reason
                    if result
                    else "The qualitative claim does not cite any stored filing passage."
                ),
                evidence_ids=result.evidence_ids if result else [],
                evaluated_evidence=[
                    {"evidence_id": evidence_id, **chunks_by_evidence_id[evidence_id]}
                    for evidence_id in filing_ids
                ],
                claimed_values=[],
                calculated_values=[],
                verifier_version=CITATION_VERIFIER_VERSION,
            )
        )
    db.add_all(evaluations)
    await db.commit()
    for evaluation in evaluations:
        await db.refresh(evaluation)
    return CitationVerificationResult(
        report_id=report_id,
        eligible_claims=len(eligible),
        verified_claims=sum(item.status == "VERIFIED" for item in evaluations),
        evaluations=[_evaluation_read(item) for item in evaluations],
    )


@router.post(
    "/reports/{report_id}/verify-contradictions",
    response_model=ContradictionVerificationResult,
)
async def verify_report_contradictions(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
) -> ContradictionVerificationResult:
    report = await db.scalar(select(ResearchReport).where(ResearchReport.id == report_id))
    if report is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    claims_result = await db.execute(
        select(ResearchClaim)
        .where(ResearchClaim.report_id == report_id)
        .order_by(ResearchClaim.claim_index)
    )
    claims = list(claims_result.scalars().all())
    eligible = [
        claim
        for claim in claims
        if is_contradiction_eligible(claim.claim_type, claim.claim_text)
    ]
    all_claim_ids = [claim.id for claim in claims]
    existing_result = await db.execute(
        select(ClaimEvaluation).where(
            ClaimEvaluation.claim_id.in_(all_claim_ids),
            ClaimEvaluation.evaluation_type == "CONTRADICTION",
        )
    ) if all_claim_ids else None
    existing = list(existing_result.scalars().all()) if existing_result is not None else []
    if not eligible:
        if existing:
            await db.execute(
                delete(ClaimEvaluation).where(
                    ClaimEvaluation.claim_id.in_(all_claim_ids),
                    ClaimEvaluation.evaluation_type == "CONTRADICTION",
                )
            )
            await db.execute(delete(ReportEvaluation).where(ReportEvaluation.report_id == report_id))
            await db.commit()
        return ContradictionVerificationResult(
            report_id=report_id,
            eligible_claims=0,
            contradicted_claims=0,
            evaluations=[],
        )

    eligible_ids = [claim.id for claim in eligible]
    existing_is_complete = (
        len(existing) == len(eligible)
        and {item.claim_id for item in existing} == set(eligible_ids)
    )
    if existing_is_complete and not force:
        ordered = sorted(existing, key=lambda evaluation: eligible_ids.index(evaluation.claim_id))
        return ContradictionVerificationResult(
            report_id=report_id,
            eligible_claims=len(eligible),
            contradicted_claims=sum(item.status == "CONTRADICTED" for item in ordered),
            evaluations=[_evaluation_read(item) for item in ordered],
        )

    company = await db.scalar(select(Company).where(Company.id == report.company_id))
    if company is None:
        raise HTTPException(status_code=409, detail="The report company no longer exists")
    report_chunk_ids = {
        int(chunk["chunk_id"])
        for chunk in report.chunks
        if chunk.get("chunk_id") is not None
    }
    evidence_by_id: dict[str, dict[str, object]] = {}
    candidate_ids_by_claim: dict[int, list[str]] = {}
    for claim in eligible:
        retrieved = await _semantic_search_for_company(
            company=company,
            query=claim.claim_text,
            db=db,
            as_of_date=report.as_of_date,
            form=None,
            limit=12,
        )
        independent = [item for item in retrieved if item.chunk_id not in report_chunk_ids][:4]
        candidate_ids: list[str] = []
        for item in independent:
            evidence_id = f"C{item.chunk_id}"
            candidate_ids.append(evidence_id)
            evidence_by_id[evidence_id] = item.model_dump(mode="json")
        candidate_ids_by_claim[claim.id] = candidate_ids

    model_inputs = [
        ContradictionClaimInput(
            claim_id=claim.id,
            claim_text=claim.claim_text,
            evidence_ids=candidate_ids_by_claim[claim.id],
        )
        for claim in eligible
        if candidate_ids_by_claim[claim.id]
    ]
    model_results = []
    if model_inputs:
        try:
            evaluator = GroqContradictionEvaluator()
            for start in range(0, len(model_inputs), CONTRADICTION_BATCH_SIZE):
                batch = model_inputs[start : start + CONTRADICTION_BATCH_SIZE]
                batch_evidence_ids = {
                    evidence_id for item in batch for evidence_id in item.evidence_ids
                }
                model_results.extend(
                    await evaluator.evaluate(
                        claims=batch,
                        evidence_by_id={
                            evidence_id: evidence_by_id[evidence_id]
                            for evidence_id in batch_evidence_ids
                        },
                    )
                )
        except ContradictionEvaluationConfigurationError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error
        except ContradictionEvaluationError as error:
            raise HTTPException(status_code=502, detail=str(error)) from error
    result_by_claim_id = {result.claim_id: result for result in model_results}

    if existing:
        await db.execute(
            delete(ClaimEvaluation).where(
                ClaimEvaluation.claim_id.in_(all_claim_ids),
                ClaimEvaluation.evaluation_type == "CONTRADICTION",
            )
        )
    await db.execute(delete(ReportEvaluation).where(ReportEvaluation.report_id == report_id))
    evaluations: list[ClaimEvaluation] = []
    for claim in eligible:
        candidate_ids = candidate_ids_by_claim[claim.id]
        result = result_by_claim_id.get(claim.id)
        evaluations.append(
            ClaimEvaluation(
                claim_id=claim.id,
                evaluation_type="CONTRADICTION",
                status=result.status if result else "UNSUPPORTED",
                confidence=result.confidence if result else 1.0,
                reason=(
                    result.reason
                    if result
                    else "No independent filing passages were available to assess for contradiction."
                ),
                evidence_ids=result.evidence_ids if result else [],
                evaluated_evidence=[
                    {"evidence_id": evidence_id, **evidence_by_id[evidence_id]}
                    for evidence_id in candidate_ids
                ],
                claimed_values=[],
                calculated_values=[],
                verifier_version=CONTRADICTION_VERIFIER_VERSION,
            )
        )
    db.add_all(evaluations)
    await db.commit()
    for evaluation in evaluations:
        await db.refresh(evaluation)
    return ContradictionVerificationResult(
        report_id=report_id,
        eligible_claims=len(eligible),
        contradicted_claims=sum(item.status == "CONTRADICTED" for item in evaluations),
        evaluations=[_evaluation_read(item) for item in evaluations],
    )


@router.get(
    "/reports/{report_id}/temporal-evaluation",
    response_model=TemporalEvaluationRead | None,
)
async def get_temporal_evaluation(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TemporalEvaluationRead | None:
    if await db.scalar(select(ResearchReport.id).where(ResearchReport.id == report_id)) is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    evaluation = await db.scalar(
        select(ReportTemporalEvaluation).where(
            ReportTemporalEvaluation.report_id == report_id
        )
    )
    return _temporal_evaluation_read(evaluation) if evaluation else None


@router.post(
    "/reports/{report_id}/verify-temporal",
    response_model=TemporalEvaluationRead,
)
async def verify_report_temporal_integrity(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
) -> TemporalEvaluationRead:
    report = await db.scalar(select(ResearchReport).where(ResearchReport.id == report_id))
    if report is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    existing = await db.scalar(
        select(ReportTemporalEvaluation).where(
            ReportTemporalEvaluation.report_id == report_id
        )
    )
    if existing is not None and not force:
        return _temporal_evaluation_read(existing)

    outcome = verify_temporal_integrity(
        as_of_date=report.as_of_date,
        sources=report.sources,
        metrics=report.metrics,
        chunks=report.chunks,
    )
    if existing is not None:
        await db.delete(existing)
    await db.execute(delete(ReportEvaluation).where(ReportEvaluation.report_id == report_id))
    evaluation = ReportTemporalEvaluation(
        report_id=report_id,
        status=outcome.status,
        score=outcome.score,
        checked_source_count=outcome.checked_source_count,
        violations=outcome.violations,
        reason=outcome.reason,
        verifier_version=TEMPORAL_VERIFIER_VERSION,
    )
    db.add(evaluation)
    await db.commit()
    await db.refresh(evaluation)
    return _temporal_evaluation_read(evaluation)


@router.get(
    "/reports/{report_id}/evaluation",
    response_model=ReportEvaluationRead | None,
)
async def get_report_evaluation(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> ReportEvaluationRead | None:
    if await db.scalar(select(ResearchReport.id).where(ResearchReport.id == report_id)) is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    evaluation = await db.scalar(
        select(ReportEvaluation).where(ReportEvaluation.report_id == report_id)
    )
    return _report_evaluation_read(evaluation) if evaluation else None


@router.post(
    "/reports/{report_id}/evaluate",
    response_model=ReportEvaluationRead,
)
async def evaluate_report(
    report_id: int,
    db: Annotated[AsyncSession, Depends(get_db)],
    force: bool = False,
) -> ReportEvaluationRead:
    report = await db.scalar(select(ResearchReport).where(ResearchReport.id == report_id))
    if report is None:
        raise HTTPException(status_code=404, detail=f"Research report {report_id} was not found")
    existing = await db.scalar(
        select(ReportEvaluation).where(ReportEvaluation.report_id == report_id)
    )
    if existing is not None and not force:
        return _report_evaluation_read(existing)

    await extract_research_claims(report_id=report_id, db=db, force=force)
    await verify_report_numeric_claims(report_id=report_id, db=db, force=force)
    await verify_report_citations(report_id=report_id, db=db, force=force)
    await verify_report_contradictions(report_id=report_id, db=db, force=force)
    await verify_report_temporal_integrity(report_id=report_id, db=db, force=force)

    claims_result = await db.execute(
        select(ResearchClaim)
        .where(ResearchClaim.report_id == report_id)
        .order_by(ResearchClaim.claim_index)
    )
    claims = list(claims_result.scalars().all())
    if not claims:
        raise HTTPException(status_code=409, detail="Extract atomic claims before scoring the report")
    claim_ids = [claim.id for claim in claims]
    evaluations_result = await db.execute(
        select(ClaimEvaluation).where(ClaimEvaluation.claim_id.in_(claim_ids))
    )
    evaluations = list(evaluations_result.scalars().all())
    temporal_evaluation = await db.scalar(
        select(ReportTemporalEvaluation).where(
            ReportTemporalEvaluation.report_id == report_id
        )
    )
    available_pairs = {
        (evaluation.claim_id, evaluation.evaluation_type) for evaluation in evaluations
    }
    required_pairs: set[tuple[int, str]] = set()
    for claim in claims:
        if is_numeric_claim(claim.claim_type, claim.claim_text):
            required_pairs.add((claim.id, "NUMERIC"))
        if claim.claim_type != "NUMERIC":
            required_pairs.add((claim.id, "CITATION"))
        if is_contradiction_eligible(claim.claim_type, claim.claim_text):
            required_pairs.add((claim.id, "CONTRADICTION"))
    missing_pairs = required_pairs - available_pairs
    if missing_pairs:
        missing_types = ", ".join(sorted({item[1].lower() for item in missing_pairs}))
        raise HTTPException(
            status_code=409,
            detail=f"Complete the missing {missing_types} claim checks before scoring the report",
        )

    score = calculate_report_score(
        claims=[
            ScoringClaim(claim_id=claim.id, citation_ids=claim.citation_ids)
            for claim in claims
        ],
        evaluations=[
            ScoringEvaluation(
                claim_id=evaluation.claim_id,
                evaluation_type=evaluation.evaluation_type,
                status=evaluation.status,
            )
            for evaluation in evaluations
        ],
        temporal_integrity_score=(
            float(temporal_evaluation.score)
            if temporal_evaluation is not None and temporal_evaluation.score is not None
            else None
        ),
    )
    existing = await db.scalar(
        select(ReportEvaluation).where(ReportEvaluation.report_id == report_id)
    )
    if existing is not None:
        await db.delete(existing)
    report_evaluation = ReportEvaluation(
        report_id=report_id,
        **score.__dict__,
        scoring_version=SCORING_VERSION,
    )
    db.add(report_evaluation)
    await db.commit()
    await db.refresh(report_evaluation)
    return _report_evaluation_read(report_evaluation)
