from datetime import date

from app.evaluation.temporal import verify_temporal_integrity


def test_temporal_verifier_passes_sources_on_or_before_cutoff() -> None:
    outcome = verify_temporal_integrity(
        as_of_date=date(2026, 5, 30),
        sources=[{"evidence_id": "M1"}, {"evidence_id": "F1"}],
        metrics=[{"filing_date": "2026-05-20"}],
        chunks=[{"filing_date": "2026-05-30"}],
    )

    assert outcome.status == "PASSED"
    assert outcome.score == 100.0
    assert outcome.checked_source_count == 2


def test_temporal_verifier_fails_future_and_missing_evidence() -> None:
    outcome = verify_temporal_integrity(
        as_of_date=date(2026, 5, 19),
        sources=[{"evidence_id": "M1"}, {"evidence_id": "F9"}],
        metrics=[{"filing_date": "2026-05-20"}],
        chunks=[],
    )

    assert outcome.status == "FAILED"
    assert outcome.score == 0.0
    assert {item["reason"] for item in outcome.violations} == {
        "AFTER_CUTOFF",
        "MISSING_EVIDENCE",
    }


def test_temporal_verifier_is_not_applicable_without_cutoff() -> None:
    outcome = verify_temporal_integrity(
        as_of_date=None,
        sources=[{"evidence_id": "F1"}],
        metrics=[],
        chunks=[{"filing_date": "2026-05-20"}],
    )

    assert outcome.status == "NOT_APPLICABLE"
    assert outcome.score is None
