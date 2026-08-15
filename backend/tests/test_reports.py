from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.company import Company
from app.models.research_report import ResearchReport


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
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(persisted)


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
