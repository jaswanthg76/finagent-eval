from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.evaluation.contradiction import (
    ContradictionClaimInput,
    ContradictionEvaluationError,
    GroqContradictionEvaluator,
    compact_counterevidence,
    is_contradiction_eligible,
    relevant_excerpt,
)


def completion(content: str) -> object:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


async def test_contradiction_evaluator_returns_guarded_results_in_claim_order() -> None:
    create = AsyncMock(
        return_value=completion(
            '{"evaluations":['
            '{"claim_id":2,"status":"CONTRADICTED","confidence":0.94,'
            '"reason":"Management said supply had materially improved.","evidence_ids":["C22"]},'
            '{"claim_id":1,"status":"UNSUPPORTED","confidence":0.8,'
            '"reason":"The candidate does not address demand.","evidence_ids":[]}'
            "]}"
        )
    )
    client = Mock()
    client.chat.completions.create = create

    results = await GroqContradictionEvaluator(client=client).evaluate(
        claims=[
            ContradictionClaimInput(1, "Demand is accelerating.", ["C11"]),
            ContradictionClaimInput(2, "Supply remains constrained.", ["C22"]),
        ],
        evidence_by_id={
            "C11": {"content": "The company discussed new products."},
            "C22": {"content": "Supply availability improved materially."},
        },
    )

    assert [result.claim_id for result in results] == [1, 2]
    assert results[1].status == "CONTRADICTED"
    assert results[1].evidence_ids == ["C22"]
    assert create.await_args.kwargs["response_format"] == {"type": "json_object"}


async def test_contradiction_evaluator_rejects_unknown_evidence_ids() -> None:
    create = AsyncMock(
        return_value=completion(
            '{"evaluations":[{"claim_id":1,"status":"CONTRADICTED",'
            '"confidence":1,"reason":"Conflict.","evidence_ids":["C99"]}]}'
        )
    )
    client = Mock()
    client.chat.completions.create = create

    with pytest.raises(ContradictionEvaluationError, match="unknown evidence IDs"):
        await GroqContradictionEvaluator(client=client).evaluate(
            claims=[ContradictionClaimInput(1, "A claim.", ["C1"])],
            evidence_by_id={"C1": {"content": "Evidence."}},
        )


def test_counterevidence_is_compacted_to_bounded_excerpts() -> None:
    compact = compact_counterevidence(
        {"C1": {"content": "A" * 20}, "C2": {"content": "B" * 20}},
        max_chars_per_passage=12,
        max_total_chars=18,
    )

    assert [len(item["content"]) for item in compact] == [9, 9]


def test_report_evidence_meta_claims_are_not_contradiction_eligible() -> None:
    assert not is_contradiction_eligible(
        "FACTUAL", "The most recent filing date in the evidence is February 24, 2023"
    )
    assert not is_contradiction_eligible(
        "OTHER",
        "The provided evidence does not explicitly state that supply remained constrained",
    )
    assert not is_contradiction_eligible(
        "MANAGEMENT_STATEMENT",
        "Management provided evidence of supply remaining constrained due to product complexity",
    )
    assert is_contradiction_eligible(
        "MANAGEMENT_STATEMENT", "NVIDIA said Blackwell supply remained constrained"
    )


def test_relevant_excerpt_finds_material_language_beyond_chunk_prefix() -> None:
    content = (
        "Generic introductory disclosure. " * 120
        + "Both Hopper and Blackwell systems have certain supply constraints, and demand "
        "for Blackwell is expected to exceed supply for several quarters."
    )

    excerpt = relevant_excerpt(
        content,
        "NVIDIA said Blackwell supply remained constrained",
        max_chars=500,
    )

    assert "Blackwell systems have certain supply constraints" in excerpt
