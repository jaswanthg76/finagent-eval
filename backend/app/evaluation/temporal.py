from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

TEMPORAL_VERIFIER_VERSION = "temporal-v1"
TemporalStatus = Literal["PASSED", "FAILED", "NOT_APPLICABLE"]


@dataclass(frozen=True)
class TemporalVerificationOutcome:
    status: TemporalStatus
    score: float | None
    checked_source_count: int
    violations: list[dict[str, str]]
    reason: str


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def verify_temporal_integrity(
    *,
    as_of_date: date | None,
    sources: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    chunks: list[dict[str, Any]],
) -> TemporalVerificationOutcome:
    if as_of_date is None:
        return TemporalVerificationOutcome(
            status="NOT_APPLICABLE",
            score=None,
            checked_source_count=0,
            violations=[],
            reason="The report has no historical as-of date to enforce.",
        )
    if not sources:
        return TemporalVerificationOutcome(
            status="NOT_APPLICABLE",
            score=None,
            checked_source_count=0,
            violations=[],
            reason="The report has no persisted evidence sources to check.",
        )

    evidence_by_id = {
        **{f"M{index}": item for index, item in enumerate(metrics, start=1)},
        **{f"F{index}": item for index, item in enumerate(chunks, start=1)},
    }
    violations: list[dict[str, str]] = []
    for source in sources:
        evidence_id = str(source.get("evidence_id", ""))
        evidence = evidence_by_id.get(evidence_id)
        if evidence is None:
            violations.append(
                {
                    "evidence_id": evidence_id or "unknown",
                    "reason": "MISSING_EVIDENCE",
                    "detail": "The source has no matching persisted evidence snapshot.",
                }
            )
            continue
        filing_date = _date(evidence.get("filing_date"))
        if filing_date is None:
            violations.append(
                {
                    "evidence_id": evidence_id,
                    "reason": "MISSING_DATE",
                    "detail": "The persisted evidence has no valid filing date.",
                }
            )
        elif filing_date > as_of_date:
            violations.append(
                {
                    "evidence_id": evidence_id,
                    "reason": "AFTER_CUTOFF",
                    "detail": (
                        f"Filing date {filing_date.isoformat()} is after the report cutoff "
                        f"{as_of_date.isoformat()}."
                    ),
                }
            )

    if violations:
        return TemporalVerificationOutcome(
            status="FAILED",
            score=0.0,
            checked_source_count=len(sources),
            violations=violations,
            reason=f"{len(violations)} of {len(sources)} persisted sources violated the cutoff.",
        )
    return TemporalVerificationOutcome(
        status="PASSED",
        score=100.0,
        checked_source_count=len(sources),
        violations=[],
        reason=f"All {len(sources)} persisted sources were filed on or before the cutoff.",
    )
