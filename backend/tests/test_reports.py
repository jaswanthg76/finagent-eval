from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.company import Company
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
    filing_dates_result = Mock()
    filing_dates_result.scalars.return_value.all.return_value = [date(2026, 5, 20)]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [company_result, filing_dates_result]

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
    assert first_search.kwargs["filed_after"] == date(2026, 5, 20)
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
