from dataclasses import dataclass

SCORING_VERSION = "reliability-v1"
COMPONENT_WEIGHTS = {
    "grounding_score": 0.35,
    "numeric_accuracy_score": 0.30,
    "citation_score": 0.20,
    "temporal_integrity_score": 0.15,
}
STATUS_VALUES = {
    "VERIFIED": 100.0,
    "PARTIALLY_SUPPORTED": 50.0,
    "UNSUPPORTED": 0.0,
    "CONTRADICTED": 0.0,
    "ERROR": 0.0,
}
STATUS_SEVERITY = {
    "VERIFIED": 0,
    "PARTIALLY_SUPPORTED": 1,
    "ERROR": 2,
    "UNSUPPORTED": 3,
    "CONTRADICTED": 4,
}


@dataclass(frozen=True)
class ScoringClaim:
    claim_id: int
    citation_ids: list[str]


@dataclass(frozen=True)
class ScoringEvaluation:
    claim_id: int
    evaluation_type: str
    status: str


@dataclass(frozen=True)
class ReportScore:
    overall_score: float
    grounding_score: float | None
    numeric_accuracy_score: float | None
    citation_score: float
    temporal_integrity_score: float | None
    total_claim_count: int
    evaluated_claim_count: int
    verified_claim_count: int
    partially_supported_claim_count: int
    unsupported_claim_count: int
    contradiction_count: int
    error_count: int


def _average_status(evaluations: list[ScoringEvaluation]) -> float | None:
    if not evaluations:
        return None
    return round(sum(STATUS_VALUES[item.status] for item in evaluations) / len(evaluations), 2)


def calculate_report_score(
    *,
    claims: list[ScoringClaim],
    evaluations: list[ScoringEvaluation],
    temporal_integrity_score: float | None = None,
) -> ReportScore:
    if not claims:
        raise ValueError("A report must have claims before it can be scored")

    grounding_score = _average_status(
        [item for item in evaluations if item.evaluation_type == "CITATION"]
    )
    numeric_accuracy_score = _average_status(
        [item for item in evaluations if item.evaluation_type == "NUMERIC"]
    )
    citation_score = round(
        sum(bool(claim.citation_ids) for claim in claims) / len(claims) * 100,
        2,
    )
    components = {
        "grounding_score": grounding_score,
        "numeric_accuracy_score": numeric_accuracy_score,
        "citation_score": citation_score,
        "temporal_integrity_score": temporal_integrity_score,
    }
    included = {
        name: value for name, value in components.items() if value is not None
    }
    total_weight = sum(COMPONENT_WEIGHTS[name] for name in included)
    overall_score = round(
        sum(value * COMPONENT_WEIGHTS[name] for name, value in included.items())
        / total_weight,
        2,
    )

    evaluations_by_claim: dict[int, list[ScoringEvaluation]] = {}
    for evaluation in evaluations:
        evaluations_by_claim.setdefault(evaluation.claim_id, []).append(evaluation)
    rolled_up_statuses: list[str] = []
    for claim in claims:
        claim_evaluations = evaluations_by_claim.get(claim.claim_id, [])
        if not claim_evaluations:
            continue
        confirmed_contradiction = any(
            item.evaluation_type == "CONTRADICTION" and item.status == "CONTRADICTED"
            for item in claim_evaluations
        )
        scored_evaluations = [
            item for item in claim_evaluations if item.evaluation_type != "CONTRADICTION"
        ]
        if confirmed_contradiction:
            rolled_up_statuses.append("CONTRADICTED")
        elif scored_evaluations:
            rolled_up_statuses.append(
                max(scored_evaluations, key=lambda item: STATUS_SEVERITY[item.status]).status
            )

    return ReportScore(
        overall_score=overall_score,
        grounding_score=grounding_score,
        numeric_accuracy_score=numeric_accuracy_score,
        citation_score=citation_score,
        temporal_integrity_score=temporal_integrity_score,
        total_claim_count=len(claims),
        evaluated_claim_count=len(rolled_up_statuses),
        verified_claim_count=rolled_up_statuses.count("VERIFIED"),
        partially_supported_claim_count=rolled_up_statuses.count("PARTIALLY_SUPPORTED"),
        unsupported_claim_count=rolled_up_statuses.count("UNSUPPORTED"),
        contradiction_count=rolled_up_statuses.count("CONTRADICTED"),
        error_count=rolled_up_statuses.count("ERROR"),
    )
