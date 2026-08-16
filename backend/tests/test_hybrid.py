from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.company import Company
from app.models.financial_metric import FinancialMetric
from app.research.hybrid import identify_metric_names


def test_identify_metric_names_from_financial_language() -> None:
    assert identify_metric_names("How did revenue and diluted EPS change?") == [
        "Revenue",
        "Diluted EPS",
    ]
    assert identify_metric_names("Review the balance sheet") == [
        "Cash and Cash Equivalents",
        "Assets",
        "Liabilities",
        "Stockholders' Equity",
    ]
    assert identify_metric_names("What risks did management discuss?") == []
    assert identify_metric_names("How quickly are receivables growing?") == [
        "Accounts Receivable"
    ]


def test_hybrid_research_returns_deduplicated_metric_evidence() -> None:
    company = Company(
        id=5,
        ticker="NVDA",
        name="NVIDIA Corporation",
        sec_cik="0001045810",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    metric = FinancialMetric(
        id=1,
        company_id=5,
        filing_id=4,
        metric_name="Revenue",
        taxonomy="us-gaap",
        xbrl_tag="RevenueFromContractWithCustomerExcludingAssessedTax",
        value=Decimal(81_615_000_000),
        unit="USD",
        period_start=date(2026, 1, 26),
        period_end=date(2026, 4, 26),
        filing_date=date(2026, 5, 20),
        fiscal_year=2027,
        fiscal_period="Q1",
        form="10-Q",
        accession_number="0001045810-26-000052",
        frame="CY2026Q1",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    company_result = Mock()
    company_result.scalar_one_or_none.return_value = company
    metrics_result = Mock()
    metrics_result.all.return_value = [
        (metric, "https://www.sec.gov/example.htm"),
        (metric, "https://www.sec.gov/example.htm"),
    ]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [company_result, metrics_result]

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch(
            "app.api.retrieval._semantic_search_for_company",
            new=AsyncMock(return_value=[]),
        ):
            response = TestClient(app).get(
                "/api/companies/NVDA/research",
                params={"query": "How did revenue change?", "form": "10-Q"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["matched_metric_names"] == ["Revenue"]
    assert len(body["metrics"]) == 1
    assert body["metrics"][0]["value"] == "81615000000"
    assert body["metrics"][0]["source_url"] == "https://www.sec.gov/example.htm"
