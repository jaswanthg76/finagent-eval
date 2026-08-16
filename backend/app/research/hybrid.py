import re

from app.research.evidence import METRIC_ALIASES
from app.research.metrics import ALLOWED_METRICS

METRIC_TERMS = tuple((metric_name, METRIC_ALIASES[metric_name]) for metric_name in ALLOWED_METRICS)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def identify_metric_names(query: str) -> list[str]:
    """Map explicit financial language to the normalized SEC metrics we store."""

    normalized_query = f" {_normalize(query)} "
    matched = [
        metric_name
        for metric_name, terms in METRIC_TERMS
        if any(f" {_normalize(term)} " in normalized_query for term in terms)
    ]

    if " balance sheet " in normalized_query:
        for metric_name in (
            "Cash and Cash Equivalents",
            "Assets",
            "Liabilities",
            "Stockholders' Equity",
        ):
            if metric_name not in matched:
                matched.append(metric_name)
    if " profitability " in normalized_query:
        for metric_name in ("Gross Profit", "Operating Income", "Net Income"):
            if metric_name not in matched:
                matched.append(metric_name)

    return matched
