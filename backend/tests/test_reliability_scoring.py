import pytest

from app.evaluation.scoring import (
    ScoringClaim,
    ScoringEvaluation,
    calculate_report_score,
)


def test_qualitative_score_uses_grounding_and_citation_coverage() -> None:
    claims = [ScoringClaim(claim_id=index, citation_ids=[f"F{index}"]) for index in range(1, 5)]
    evaluations = [
        ScoringEvaluation(index, "CITATION", "VERIFIED") for index in range(1, 4)
    ] + [ScoringEvaluation(4, "CITATION", "PARTIALLY_SUPPORTED")]

    score = calculate_report_score(claims=claims, evaluations=evaluations)

    assert score.grounding_score == 87.5
    assert score.numeric_accuracy_score is None
    assert score.citation_score == 100.0
    assert score.temporal_integrity_score is None
    assert score.overall_score == 92.05
    assert score.verified_claim_count == 3
    assert score.partially_supported_claim_count == 1


def test_numeric_score_does_not_invent_unimplemented_components() -> None:
    score = calculate_report_score(
        claims=[ScoringClaim(1, ["M1", "M2"])],
        evaluations=[ScoringEvaluation(1, "NUMERIC", "VERIFIED")],
    )

    assert score.overall_score == 100.0
    assert score.numeric_accuracy_score == 100.0
    assert score.grounding_score is None
    assert score.temporal_integrity_score is None


def test_completed_temporal_score_participates_in_overall_score() -> None:
    score = calculate_report_score(
        claims=[ScoringClaim(1, ["F1"])],
        evaluations=[ScoringEvaluation(1, "CITATION", "VERIFIED")],
        temporal_integrity_score=0.0,
    )

    assert score.temporal_integrity_score == 0.0
    assert score.overall_score == 78.57


def test_claim_status_counts_use_worst_evaluation_per_claim() -> None:
    score = calculate_report_score(
        claims=[ScoringClaim(1, ["M1", "F1"])],
        evaluations=[
            ScoringEvaluation(1, "NUMERIC", "VERIFIED"),
            ScoringEvaluation(1, "CITATION", "UNSUPPORTED"),
        ],
    )

    assert score.evaluated_claim_count == 1
    assert score.verified_claim_count == 0
    assert score.unsupported_claim_count == 1


def test_only_confirmed_counterevidence_changes_claim_status() -> None:
    score = calculate_report_score(
        claims=[ScoringClaim(1, ["F1"]), ScoringClaim(2, ["F2"])],
        evaluations=[
            ScoringEvaluation(1, "CITATION", "VERIFIED"),
            ScoringEvaluation(1, "CONTRADICTION", "UNSUPPORTED"),
            ScoringEvaluation(2, "CITATION", "VERIFIED"),
            ScoringEvaluation(2, "CONTRADICTION", "CONTRADICTED"),
        ],
    )

    assert score.verified_claim_count == 1
    assert score.contradiction_count == 1
    assert score.grounding_score == 100.0


def test_report_without_claims_cannot_be_scored() -> None:
    with pytest.raises(ValueError, match="must have claims"):
        calculate_report_score(claims=[], evaluations=[])
