from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.main import app
from app.models.research_claim import ResearchClaim
from app.models.research_report import ResearchReport
from app.research.claims import ClaimExtractionError, GroqClaimExtractor
from app.schemas.research import ExtractedClaim


def completion(content: str) -> object:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


async def test_claim_extractor_returns_atomic_validated_claims() -> None:
    create = AsyncMock(
        return_value=completion(
            """{"claims":[
                {"claim_text":"Revenue increased 85%.","claim_type":"NUMERIC","citation_ids":["m1","M2"]},
                {"claim_text":"Revenue increased 85%.","claim_type":"NUMERIC","citation_ids":["M1"]},
                {"claim_text":"Management cited demand.","claim_type":"MANAGEMENT_STATEMENT","citation_ids":["F1"]}
            ]}"""
        )
    )
    client = Mock()
    client.chat.completions.create = create

    claims = await GroqClaimExtractor(client=client).extract(
        answer="Revenue increased 85% [M1][M2]. Management cited demand [F1].",
        available_evidence_ids={"M1", "M2", "F1"},
    )

    assert len(claims) == 2
    assert claims[0].citation_ids == ["M1", "M2"]
    assert claims[1].claim_type == "MANAGEMENT_STATEMENT"
    assert create.await_args.kwargs["response_format"] == {"type": "json_object"}


async def test_claim_extractor_rejects_unknown_citations() -> None:
    create = AsyncMock(
        return_value=completion(
            '{"claims":[{"claim_text":"A claim.","claim_type":"FACTUAL",'
            '"citation_ids":["F99"]}]}'
        )
    )
    client = Mock()
    client.chat.completions.create = create

    with pytest.raises(ClaimExtractionError, match="unknown evidence IDs"):
        await GroqClaimExtractor(client=client).extract(
            answer="A claim.",
            available_evidence_ids={"F1"},
        )


def test_extract_and_list_persisted_research_claims() -> None:
    report = ResearchReport(
        id=1,
        company_id=5,
        question="How did revenue change?",
        as_of_date=None,
        form="10-Q",
        answer="Revenue increased 85% [M1][M2].",
        provider="groq",
        model="llama-3.3-70b-versatile",
        tool_calls=[],
        sources=[
            {"evidence_id": "M1", "kind": "metric", "title": "Revenue", "source_url": None},
            {"evidence_id": "M2", "kind": "metric", "title": "Revenue", "source_url": None},
        ],
        metrics=[],
        chunks=[],
        created_at=datetime(2026, 8, 16, tzinfo=UTC),
    )
    empty_claims = Mock()
    empty_claims.scalars.return_value.all.return_value = []
    saved_claims = Mock()
    session = AsyncMock(spec=AsyncSession)
    session.scalar.side_effect = [report, 1]
    session.execute.side_effect = [empty_claims, saved_claims]

    async def assign_database_values(claim: ResearchClaim) -> None:
        claim.id = 10
        claim.created_at = datetime(2026, 8, 16, 14, 0, tzinfo=UTC)

    session.refresh.side_effect = assign_database_values
    extracted = ExtractedClaim(
        claim_text="Revenue increased 85%.",
        claim_type="NUMERIC",
        citation_ids=["M1", "M2"],
    )
    fake_extractor = Mock()
    fake_extractor.extract = AsyncMock(return_value=[extracted])

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        with patch("app.api.research.GroqClaimExtractor", return_value=fake_extractor):
            extract_response = TestClient(app).post("/api/research/reports/1/claims/extract")

        persisted = session.add_all.call_args.args[0][0]
        saved_claims.scalars.return_value.all.return_value = [persisted]
        list_response = TestClient(app).get("/api/research/reports/1/claims")
    finally:
        app.dependency_overrides.clear()

    assert extract_response.status_code == 200
    assert extract_response.json()["extracted"] == 1
    assert persisted.claim_index == 1
    assert persisted.extraction_metadata["prompt_version"] == "claims-v1"
    assert list_response.status_code == 200
    assert list_response.json()[0]["citation_ids"] == ["M1", "M2"]
