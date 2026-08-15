from app.evaluation.numeric import extract_claimed_values, verify_numeric_claim

METRICS = {
    "M1": {
        "metric_name": "Revenue",
        "value": "81615000000.000000",
        "unit": "USD",
        "period_end": "2026-04-26",
    },
    "M2": {
        "metric_name": "Revenue",
        "value": "44062000000.000000",
        "unit": "USD",
        "period_end": "2025-04-27",
    },
}


def test_extract_claimed_percentages_and_scaled_amounts() -> None:
    assert extract_claimed_values("Revenue increased $37.553 billion, or 85.2%.") == [
        {"kind": "percent", "value": "85.2", "text": "85.2%"},
        {"kind": "amount", "value": "37553000000.000", "text": "$37.553 billion"},
    ]


def test_numeric_verifier_recalculates_and_verifies_comparison() -> None:
    outcome = verify_numeric_claim(
        claim_text="Revenue increased $37.553 billion, or about 85%.",
        citation_ids=["M1", "M2"],
        metrics_by_evidence_id=METRICS,
    )

    assert outcome.status == "VERIFIED"
    assert outcome.confidence == 1.0
    assert any(value["label"] == "percentage change" for value in outcome.calculated_values)


def test_numeric_verifier_marks_wrong_value_contradicted() -> None:
    outcome = verify_numeric_claim(
        claim_text="Revenue increased 50%.",
        citation_ids=["M1", "M2"],
        metrics_by_evidence_id=METRICS,
    )

    assert outcome.status == "CONTRADICTED"


def test_numeric_verifier_requires_metric_citations() -> None:
    outcome = verify_numeric_claim(
        claim_text="Revenue increased 85%.",
        citation_ids=["F1"],
        metrics_by_evidence_id=METRICS,
    )

    assert outcome.status == "UNSUPPORTED"
