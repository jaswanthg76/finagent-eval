from collections.abc import AsyncIterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.company import Company


def test_list_companies() -> None:
    company = Company(
        id=1,
        ticker="NVDA",
        name="NVIDIA Corporation",
        sec_cik="0001045810",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    result = Mock()
    result.scalars.return_value.all.return_value = [company]
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = result

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/api/companies")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": 1,
            "ticker": "NVDA",
            "name": "NVIDIA Corporation",
            "sec_cik": "0001045810",
            "created_at": "2026-08-15T00:00:00Z",
        }
    ]
