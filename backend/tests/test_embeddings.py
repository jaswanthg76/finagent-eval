from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.main import app
from app.models.company import Company
from app.models.filing_chunk import FilingChunk
from app.retrieval.embeddings import LocalEmbeddingClient, OpenAIEmbeddingClient, content_hash


def _company() -> Company:
    return Company(
        id=5,
        ticker="NVDA",
        name="NVIDIA Corporation",
        sec_cik="0001045810",
        created_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_embedding_client_requests_configured_dimensions_and_orders_results() -> None:
    response = SimpleNamespace(
        data=[
            SimpleNamespace(index=1, embedding=[2.0] * settings.embedding_dimensions),
            SimpleNamespace(index=0, embedding=[1.0] * settings.embedding_dimensions),
        ]
    )
    sdk_client = SimpleNamespace(
        embeddings=SimpleNamespace(create=AsyncMock(return_value=response))
    )

    vectors = await OpenAIEmbeddingClient(
        client=sdk_client, model_name="text-embedding-3-small"
    ).embed_texts(["first", "second"])

    assert vectors[0][0] == 1.0
    assert vectors[1][0] == 2.0
    sdk_client.embeddings.create.assert_awaited_once_with(
        model="text-embedding-3-small",
        input=["first", "second"],
        dimensions=512,
        encoding_format="float",
    )


@pytest.mark.asyncio
async def test_local_embedding_client_returns_plain_vectors() -> None:
    local_model = Mock()
    local_model.embed.return_value = [
        SimpleNamespace(tolist=lambda: [1.0] * settings.embedding_dimensions),
        SimpleNamespace(tolist=lambda: [2.0] * settings.embedding_dimensions),
    ]

    vectors = await LocalEmbeddingClient(model=local_model).embed_texts(["first", "second"])

    assert vectors[0][0] == 1.0
    assert vectors[1][0] == 2.0
    local_model.embed.assert_called_once_with(["first", "second"])


def test_content_hash_changes_with_content() -> None:
    assert content_hash("first") == content_hash("first")
    assert content_hash("first") != content_hash("second")


def test_sync_chunk_embeddings_skips_unchanged_chunks() -> None:
    pending = FilingChunk(
        id=1,
        section_id=1,
        chunk_index=0,
        content="Pending filing evidence.",
        token_count=4,
    )
    unchanged = FilingChunk(
        id=2,
        section_id=1,
        chunk_index=1,
        content="Already embedded evidence.",
        token_count=4,
        embedding=[0.0] * settings.embedding_dimensions,
        embedding_model=settings.embedding_model,
        embedding_content_hash=content_hash("Already embedded evidence."),
        embedded_at=datetime(2026, 8, 15, tzinfo=UTC),
    )
    company_result = Mock()
    company_result.scalar_one_or_none.return_value = _company()
    chunks_result = Mock()
    chunks_result.scalars.return_value.all.return_value = [pending, unchanged]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [company_result, chunks_result]

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    embedding_client = Mock()
    embedding_client.embed_texts = AsyncMock(
        return_value=[[0.1] * settings.embedding_dimensions]
    )
    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.api.retrieval.create_embedding_client", return_value=embedding_client):
            response = TestClient(app).post("/api/companies/NVDA/embeddings/sync")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["embedded"] == 1
    assert response.json()["skipped"] == 1
    assert response.json()["remaining"] == 0
    assert pending.embedding_model == settings.embedding_model
    assert pending.embedding_content_hash == content_hash(pending.content)
    session.commit.assert_awaited_once()


def test_semantic_search_returns_grounded_source_metadata() -> None:
    company_result = Mock()
    company_result.scalar_one_or_none.return_value = _company()
    search_result = [
        SimpleNamespace(
            chunk_id=10,
            filing_id=4,
            accession_number="0001045810-26-000052",
            form="10-Q",
            filing_date=date(2026, 5, 20),
            report_date=date(2026, 4, 26),
            section_name="Management's Discussion and Analysis",
            chunk_index=3,
            content="Revenue increased due to accelerated computing demand.",
            token_count=9,
            distance=0.2,
            source_url="https://www.sec.gov/example.htm",
            embedded_at=datetime(2026, 8, 15, tzinfo=UTC),
        )
    ]
    session = AsyncMock(spec=AsyncSession)
    session.execute.side_effect = [company_result, search_result]
    session.scalar.return_value = 1

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    embedding_client = Mock()
    embedding_client.embed_texts = AsyncMock(
        return_value=[[0.1] * settings.embedding_dimensions]
    )
    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.api.retrieval.create_embedding_client", return_value=embedding_client):
            response = TestClient(app).get(
                "/api/companies/NVDA/search",
                params={"query": "Why did revenue increase?", "limit": 5},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body[0]["chunk_id"] == 10
    assert body[0]["similarity"] == pytest.approx(0.8)
    assert body[0]["section_name"] == "Management's Discussion and Analysis"
    assert body[0]["source_url"] == "https://www.sec.gov/example.htm"
