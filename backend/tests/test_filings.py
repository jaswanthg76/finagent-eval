from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Self
from unittest.mock import AsyncMock, Mock, patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.ingestion.sec_client import SECDocumentError
from app.main import app
from app.models.filing import Filing
from app.schemas.filing import FilingIngestResult


class FakeSECClient:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def _filing(filing_id: int) -> Filing:
    return Filing(
        id=filing_id,
        company_id=1,
        accession_number=f"0000000000-26-{filing_id:06d}",
        form="10-Q",
        filing_date=date(2026, 8, 15),
        report_date=date(2026, 7, 31),
        primary_document=f"filing-{filing_id}.htm",
        document_url=f"https://www.sec.gov/filing-{filing_id}.htm",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


def test_reingest_all_continues_after_individual_failure() -> None:
    filings = [_filing(1), _filing(2)]
    query_result = Mock()
    query_result.scalars.return_value.all.return_value = filings
    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = query_result

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    successful_result = FilingIngestResult(
        filing_id=1,
        sections_created=3,
        chunks_created=12,
        ingested_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    ingest = AsyncMock(
        side_effect=[successful_result, SECDocumentError("bad filing")]
    )
    app.dependency_overrides[get_db] = override_get_db
    try:
        with (
            patch("app.api.filings.SECClient", return_value=FakeSECClient()),
            patch("app.api.filings._ingest_filing_content", ingest),
        ):
            response = TestClient(app).post("/api/filings/reingest-all")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["succeeded"] == 1
    assert response.json()["failed"] == 1
    assert response.json()["failures"] == [{"filing_id": 2, "detail": "bad filing"}]
