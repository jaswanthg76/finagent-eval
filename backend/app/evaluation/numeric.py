import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

NUMERIC_VERIFIER_VERSION = "numeric-v2"
VerificationStatus = Literal[
    "VERIFIED",
    "PARTIALLY_SUPPORTED",
    "UNSUPPORTED",
    "CONTRADICTED",
    "ERROR",
]

PERCENT_RE = re.compile(
    r"(?<![\d.])(-?[\d,]+(?:\.\d+)?)\s*(?:%|\bpercent\b)", re.IGNORECASE
)
AMOUNT_RE = re.compile(
    r"(?:\$\s*(?P<currency_value>-?[\d,]+(?:\.\d+)?)\s*"
    r"(?P<currency_scale>trillion|billion|million|thousand|[TBMK])?\b)"
    r"|(?:\b(?P<scaled_value>-?[\d,]+(?:\.\d+)?)\s+"
    r"(?P<scaled_scale>trillion|billion|million|thousand)\b)",
    re.IGNORECASE,
)
SCALE_FACTORS = {
    "t": Decimal(1000000000000),
    "trillion": Decimal(1000000000000),
    "b": Decimal(1000000000),
    "billion": Decimal(1000000000),
    "m": Decimal(1000000),
    "million": Decimal(1000000),
    "k": Decimal(1000),
    "thousand": Decimal(1000),
}
DIRECTION_RE = re.compile(
    r"\b(?:(?P<decrease>decreased?|declined?|fell|fallen|dropped?|down|reduced?|reduction)"
    r"|(?P<increase>increased?|grew|grown|rose|up))\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class NumericVerificationOutcome:
    status: VerificationStatus
    confidence: float
    reason: str
    claimed_values: list[dict[str, Any]]
    calculated_values: list[dict[str, Any]]


def _decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def extract_claimed_values(text: str) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for match in PERCENT_RE.finditer(text):
        try:
            value = _decimal(match.group(1))
        except InvalidOperation:
            continue
        direction_matches = list(DIRECTION_RE.finditer(text[max(0, match.start() - 80) : match.start()]))
        if value > 0 and direction_matches and direction_matches[-1].group("decrease"):
            value = -value
        values.append({"kind": "percent", "value": str(value), "text": match.group(0)})
        occupied.append(match.span())

    for match in AMOUNT_RE.finditer(text):
        if any(match.start() < end and match.end() > start for start, end in occupied):
            continue
        raw_value = match.group("currency_value") or match.group("scaled_value")
        raw_scale = match.group("currency_scale") or match.group("scaled_scale")
        try:
            value = _decimal(raw_value)
        except InvalidOperation:
            continue
        if raw_scale:
            value *= SCALE_FACTORS[raw_scale.lower()]
        values.append({"kind": "amount", "value": str(value), "text": match.group(0)})
    return values


def is_numeric_claim(claim_type: str, claim_text: str) -> bool:
    return claim_type in {"NUMERIC", "COMPARATIVE"} or bool(extract_claimed_values(claim_text))


def _metric_value(metric: dict[str, Any]) -> Decimal | None:
    try:
        return Decimal(str(metric["value"]))
    except (InvalidOperation, KeyError):
        return None


def _calculated_values(metrics: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    calculated: list[dict[str, Any]] = []
    sorted_metrics = sorted(metrics, key=lambda item: str(item[1].get("period_end", "")), reverse=True)
    for evidence_id, metric in sorted_metrics:
        value = _metric_value(metric)
        if value is None:
            continue
        calculated.append(
            {
                "kind": "amount",
                "value": str(value),
                "evidence_ids": [evidence_id],
                "label": f"{metric.get('metric_name', 'metric')} for {metric.get('period_end', 'period')}",
            }
        )

    if len(sorted_metrics) >= 2:
        newer_id, newer = sorted_metrics[0]
        newer_value = _metric_value(newer)
        for older_id, older in sorted_metrics[1:]:
            older_value = _metric_value(older)
            if (
                newer_value is None
                or older_value is None
                or newer.get("metric_name") != older.get("metric_name")
                or newer.get("unit") != older.get("unit")
            ):
                continue
            change = newer_value - older_value
            calculated.append(
                {
                    "kind": "amount",
                    "value": str(change),
                    "evidence_ids": [newer_id, older_id],
                    "label": "absolute change",
                }
            )
            if older_value != 0:
                percent = (change / abs(older_value) * Decimal(100)).quantize(Decimal("0.1"))
                calculated.append(
                    {
                        "kind": "percent",
                        "value": str(percent),
                        "evidence_ids": [newer_id, older_id],
                        "label": "percentage change",
                    }
                )
    return calculated


def _filing_values(filings: list[tuple[str, dict[str, Any]]]) -> list[dict[str, Any]]:
    calculated: list[dict[str, Any]] = []
    for evidence_id, filing in filings:
        for value in extract_claimed_values(str(filing.get("content", ""))):
            calculated.append(
                {
                    "kind": value["kind"],
                    "value": value["value"],
                    "evidence_ids": [evidence_id],
                    "label": "value stated in cited filing passage",
                }
            )
    return calculated


def _matches(claimed: dict[str, Any], calculated: dict[str, Any]) -> bool:
    if claimed["kind"] != calculated["kind"]:
        return False
    claimed_value = Decimal(str(claimed["value"]))
    calculated_value = Decimal(str(calculated["value"]))
    if claimed["kind"] == "percent":
        return abs(claimed_value - calculated_value) <= Decimal("1.0")
    tolerance = max(abs(calculated_value) * Decimal("0.01"), Decimal("0.01"))
    return abs(claimed_value - calculated_value) <= tolerance


def verify_numeric_claim(
    *,
    claim_text: str,
    citation_ids: list[str],
    metrics_by_evidence_id: dict[str, dict[str, Any]],
    filings_by_evidence_id: dict[str, dict[str, Any]] | None = None,
) -> NumericVerificationOutcome:
    claimed = extract_claimed_values(claim_text)
    cited_metrics = [
        (evidence_id, metrics_by_evidence_id[evidence_id])
        for evidence_id in citation_ids
        if evidence_id in metrics_by_evidence_id
    ]
    available_filings = filings_by_evidence_id or {}
    cited_filings = [
        (evidence_id, available_filings[evidence_id])
        for evidence_id in citation_ids
        if evidence_id in available_filings
    ]
    if not claimed:
        return NumericVerificationOutcome(
            status="ERROR",
            confidence=1.0,
            reason="No explicit percentage or scaled/currency amount could be parsed from the claim.",
            claimed_values=[],
            calculated_values=[],
        )
    if not cited_metrics and not cited_filings:
        return NumericVerificationOutcome(
            status="UNSUPPORTED",
            confidence=1.0,
            reason="The numeric claim does not cite any stored metric or filing evidence.",
            claimed_values=claimed,
            calculated_values=[],
        )

    calculated = _calculated_values(cited_metrics) + _filing_values(cited_filings)
    matched = [any(_matches(value, candidate) for candidate in calculated) for value in claimed]
    matched_count = sum(matched)
    if matched_count == len(claimed):
        status: VerificationStatus = "VERIFIED"
        confidence = 1.0
        reason = "Every parsed numeric value matches the cited metric facts or a deterministic comparison."
    elif matched_count > 0:
        status = "PARTIALLY_SUPPORTED"
        confidence = matched_count / len(claimed)
        reason = f"{matched_count} of {len(claimed)} parsed numeric values matched cited metric evidence."
    else:
        status = "CONTRADICTED"
        confidence = 1.0
        reason = "None of the parsed numeric values matched the cited metric facts or comparisons."
    return NumericVerificationOutcome(
        status=status,
        confidence=confidence,
        reason=reason,
        claimed_values=claimed,
        calculated_values=calculated,
    )
