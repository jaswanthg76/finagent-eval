from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.research import evaluate_report
from app.core.database import get_db
from app.main import app
from app.models.claim_evaluation import ClaimEvaluation
from app.models.company import Company
from app.models.report_evaluation import ReportEvaluation
from app.models.report_temporal_evaluation import ReportTemporalEvaluation
from app.models.research_claim import ResearchClaim
from app.models.research_report import ResearchReport
from app.schemas.retrieval import MetricEvidence


def stored_report() -> ResearchReport:
    return ResearchReport(
        id=12,
        company_id=5,
        question="What risks did management discuss?",
        as_of_date=date(2026, 5, 30),
        form="10-Q",
        answer="Management discussed supply risk [F1].",
        provider="groq",
        model="llama-3.3-70b-versatile",
        tool_calls=[{"name": "search_filings", "arguments": {"query": "supply risk"}}],
        sources=[
            {
                "evidence_id": "F1",
                "kind": "filing",
                "title": "10-Q · Risk Factors",
                "source_url": "https://www.sec.gov/example.htm",
            }
        ],
        metrics=[],
        chunks=[],
        created_at=datetime(2026, 8, 16, 12, 0, tzinfo=UTC),
    )


def test_generate_research_answer_persists_report_snapshot() -> None:
    company = Company(
        id=5,
        ticker="NVDA",
        name="NVIDIA Corporation",
        sec_cik="0001045810",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    company_result = Mock()
    company_result.scalar_one_or_none.return_value = company
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = company_result

    async def assign_database_values(report: ResearchReport) -> None:
        report.id = 20
        report.created_at = datetime(2026, 8, 16, 13, 0, tzinfo=UTC)

    session.refresh.side_effect = assign_database_values
    fake_agent = Mock()
    fake_agent.answer = AsyncMock(return_value=("No material change was identified.", []))

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.api.research.GroqResearchAgent", return_value=fake_agent):
            response = TestClient(app).post(
                "/api/research",
                json={"ticker": "NVDA", "question": "What changed in management's outlook?"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["id"] == 20
    persisted = session.add.call_args.args[0]
    assert isinstance(persisted, ResearchReport)
    assert persisted.company_id == 5
    assert persisted.answer == "No material change was identified."
    assert persisted.sources == []
    assert "required_metric_names" not in fake_agent.answer.await_args.kwargs
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(persisted)


def test_research_aligns_filing_search_with_latest_metric_filing() -> None:
    company = Company(
        id=5,
        ticker="NVDA",
        name="NVIDIA Corporation",
        sec_cik="0001045810",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    company_result = Mock()
    company_result.scalar_one_or_none.return_value = company
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = company_result

    async def assign_database_values(report: ResearchReport) -> None:
        report.id = 21
        report.created_at = datetime(2026, 8, 16, 14, 0, tzinfo=UTC)

    session.refresh.side_effect = assign_database_values

    metric = MetricEvidence(
        metric_id=2308,
        metric_name="Revenue",
        value=Decimal(81615000000),
        unit="USD",
        period_start=date(2026, 1, 26),
        period_end=date(2026, 4, 26),
        filing_date=date(2026, 5, 20),
        fiscal_year=2027,
        fiscal_period="Q1",
        form="10-Q",
        accession_number="0001045810-26-000052",
        source_url="https://www.sec.gov/latest.htm",
    )

    async def answer(**kwargs: object) -> tuple[str, list[dict[str, object]]]:
        execute_tool = kwargs["execute_tool"]
        await execute_tool("search_filings", {"query": "revenue drivers", "limit": 2})
        await execute_tool(
            "get_financial_metrics",
            {"metric_names": ["Revenue"], "limit_per_metric": 2},
        )
        await execute_tool("search_filings", {"query": "revenue drivers", "limit": 2})
        return "Revenue increased [M1].", []

    fake_agent = Mock()
    fake_agent.answer = AsyncMock(side_effect=answer)

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    semantic_search = AsyncMock(return_value=[])
    try:
        with (
            patch("app.api.research.GroqResearchAgent", return_value=fake_agent),
            patch("app.api.research._metric_evidence", new=AsyncMock(return_value=[metric])),
            patch("app.api.research._semantic_search_for_company", new=semantic_search),
        ):
            response = TestClient(app).post(
                "/api/research",
                json={
                    "ticker": "NVDA",
                    "question": "How did latest revenue change, and what drove it?",
                    "form": "10-Q",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    first_search, aligned_search = semantic_search.await_args_list
    assert first_search.kwargs["filed_after"] is None
    assert first_search.kwargs["accession_numbers"] is None
    assert aligned_search.kwargs["filed_after"] == date(2026, 5, 20)
    assert aligned_search.kwargs["accession_numbers"] == {
        "0001045810-26-000052"
    }


def test_list_and_get_saved_research_reports() -> None:
    report = stored_report()
    list_result = Mock()
    list_result.all.return_value = [(report, "NVDA")]
    detail_result = Mock()
    detail_result.one_or_none.return_value = (report, "NVDA")
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [list_result, detail_result]

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        client = TestClient(app)
        list_response = client.get("/api/research/reports", params={"ticker": "nvda"})
        detail_response = client.get("/api/research/reports/12")
    finally:
        app.dependency_overrides.clear()

    assert list_response.status_code == 200
    summary = list_response.json()[0]
    assert summary["id"] == 12
    assert summary["source_count"] == 1
    assert summary["ticker"] == "NVDA"

    assert detail_response.status_code == 200
    detail = detail_response.json()
    assert detail["answer"] == "Management discussed supply risk [F1]."
    assert detail["sources"][0]["evidence_id"] == "F1"


async def test_evaluate_report_runs_the_full_pipeline_before_scoring() -> None:
    report = stored_report()
    claim = ResearchClaim(
        id=31,
        report_id=report.id,
        claim_index=1,
        claim_text="Management discussed supply risk.",
        claim_type="FACTUAL",
        citation_ids=["F1"],
        extraction_metadata={},
    )
    evaluations = [
        ClaimEvaluation(
            id=41,
            claim_id=claim.id,
            evaluation_type="CITATION",
            status="VERIFIED",
            confidence=1.0,
            reason="Supported.",
            evidence_ids=["F1"],
            evaluated_evidence=[],
            claimed_values=[],
            calculated_values=[],
            verifier_version="test",
        ),
        ClaimEvaluation(
            id=42,
            claim_id=claim.id,
            evaluation_type="CONTRADICTION",
            status="UNSUPPORTED",
            confidence=1.0,
            reason="No contradiction found.",
            evidence_ids=[],
            evaluated_evidence=[],
            claimed_values=[],
            calculated_values=[],
            verifier_version="test",
        ),
    ]
    temporal = ReportTemporalEvaluation(
        id=51,
        report_id=report.id,
        status="PASSED",
        score=100.0,
        checked_source_count=1,
        violations=[],
        reason="All evidence is in scope.",
        verifier_version="test",
    )
    claims_result = Mock()
    claims_result.scalars.return_value.all.return_value = [claim]
    evaluations_result = Mock()
    evaluations_result.scalars.return_value.all.return_value = evaluations
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [report, None, temporal, None]
    session.execute.side_effect = [claims_result, evaluations_result]

    async def assign_database_values(evaluation: ReportEvaluation) -> None:
        evaluation.id = 61
        evaluation.created_at = datetime(2026, 8, 16, 15, 0, tzinfo=UTC)

    session.refresh.side_effect = assign_database_values
    call_order: list[str] = []
    stages = (
        "extract_research_claims",
        "verify_report_numeric_claims",
        "verify_report_citations",
        "verify_report_contradictions",
        "verify_report_temporal_integrity",
    )
    patches = [
        patch(
            f"app.api.research.{stage}",
            new=AsyncMock(side_effect=lambda *args, _stage=stage, **kwargs: call_order.append(_stage)),
        )
        for stage in stages
    ]

    for stage_patch in patches:
        stage_patch.start()
    try:
        result = await evaluate_report(report_id=report.id, db=session, force=False)
    finally:
        for stage_patch in reversed(patches):
            stage_patch.stop()

    assert call_order == list(stages)
    assert result.report_id == report.id
    assert result.overall_score == 100.0
    assert result.temporal_integrity_score == 100.0


async def test_evaluate_report_returns_existing_score_without_rerunning_pipeline() -> None:
    existing = ReportEvaluation(
        id=61,
        report_id=12,
        overall_score=100.0,
        grounding_score=100.0,
        numeric_accuracy_score=None,
        citation_score=100.0,
        temporal_integrity_score=100.0,
        total_claim_count=1,
        evaluated_claim_count=1,
        verified_claim_count=1,
        partially_supported_claim_count=0,
        unsupported_claim_count=0,
        contradiction_count=0,
        error_count=0,
        scoring_version="test",
        created_at=datetime(2026, 8, 16, 15, 0, tzinfo=UTC),
    )
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [stored_report(), existing]
    extract = AsyncMock()

    with patch("app.api.research.extract_research_claims", new=extract):
        result = await evaluate_report(report_id=12, db=session, force=False)

    assert result.id == existing.id
    extract.assert_not_awaited()
