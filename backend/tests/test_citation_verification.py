from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.evaluation.citations import (
    CitationClaimInput,
    CitationEvaluationError,
    GroqCitationEvaluator,
    compact_filing_evidence,
)


def completion(content: str) -> object:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


async def test_citation_evaluator_returns_guarded_results_in_claim_order() -> None:
    create = AsyncMock(
        return_value=completion(
            '{"evaluations":['
            '{"claim_id":2,"status":"UNSUPPORTED","confidence":0.9,'
            '"reason":"The passage does not identify demand as the cause."},'
            '{"claim_id":1,"status":"VERIFIED","confidence":0.98,'
            '"reason":"The passage explicitly identifies long manufacturing lead times."}'
            "]}"
        )
    )
    client = Mock()
    client.chat.completions.create = create

    results = await GroqCitationEvaluator(client=client).evaluate(
        claims=[
            CitationClaimInput(1, "Lead times are long.", ["F1"]),
            CitationClaimInput(2, "Demand caused the constraint.", ["F1"]),
        ],
        evidence_by_id={
            "F1": {
                "section_name": "Risk Factors",
                "filing_date": "2026-05-20",
                "content": "Manufacturing lead times are long and availability is uncertain.",
            }
        },
    )

    assert [result.claim_id for result in results] == [1, 2]
    assert results[0].status == "VERIFIED"
    assert results[1].status == "UNSUPPORTED"
    assert create.await_args.kwargs["response_format"] == {"type": "json_object"}


async def test_citation_evaluator_rejects_missing_or_extra_claim_ids() -> None:
    create = AsyncMock(
        return_value=completion(
            '{"evaluations":[{"claim_id":99,"status":"VERIFIED",'
            '"confidence":1,"reason":"Wrong claim."}]}'
        )
    )
    client = Mock()
    client.chat.completions.create = create

    with pytest.raises(CitationEvaluationError, match="exactly the requested claim IDs"):
        await GroqCitationEvaluator(client=client).evaluate(
            claims=[CitationClaimInput(1, "A claim.", ["F1"])],
            evidence_by_id={"F1": {"content": "Evidence."}},
        )


def test_filing_evidence_is_compacted_to_bounded_excerpts() -> None:
    compact = compact_filing_evidence(
        {
            "F1": {"content": "A" * 20},
            "F2": {"content": "B" * 20},
        },
        max_chars_per_passage=12,
        max_total_chars=18,
    )

    assert [len(item["content"]) for item in compact] == [9, 9]
