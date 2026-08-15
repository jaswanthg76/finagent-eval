from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.company import Company
from app.models.financial_metric import FinancialMetric


def test_list_financial_metrics() -> None:
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
    metrics_result.scalars.return_value.all.return_value = [metric]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [company_result, metrics_result]

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get(
            "/api/companies/NVDA/metrics",
            params={
                "metric_name": "Revenue",
                "as_of_date": "2026-05-20",
                "fiscal_period": "q1",
                "limit": 10,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["metric_name"] == "Revenue"
    assert body[0]["value"] == "81615000000"
    assert body[0]["period_start"] == "2026-01-26"
    assert body[0]["fiscal_year"] == 2027

