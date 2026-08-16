import re

from app.research.metrics import ALLOWED_METRICS

METRIC_ALIASES: dict[str, tuple[str, ...]] = {
    "Revenue": ("revenue", "revenues", "sales", "top line"),
    "Gross Profit": ("gross profit",),
    "Operating Income": ("operating income", "operating profit"),
    "Net Income": ("net income", "net earnings", "bottom line", "profit"),
    "Diluted EPS": ("diluted eps", "eps", "earnings per share"),
    "Cash and Cash Equivalents": (
        "cash and cash equivalents",
        "cash balance",
        "cash position",
        "cash equivalents",
    ),
    "Operating Cash Flow": (
        "operating cash flow",
        "cash from operations",
        "net cash provided by operating activities",
    ),
    "Capital Expenditures": ("capital expenditures", "capital expenditure", "capex"),
    "Research and Development Expense": (
        "research and development",
        "research and development expense",
        "r&d expense",
        "r&d spending",
    ),
    "Accounts Receivable": (
        "accounts receivable",
        "receivables",
        "trade receivables",
    ),
    "Assets": ("assets", "total assets"),
    "Liabilities": ("liabilities", "total liabilities"),
    "Stockholders' Equity": (
        "stockholders equity",
        "shareholders equity",
        "book value",
    ),
}


def _normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


_METRIC_BY_NORMALIZED_CONCEPT = {
    _normalize(alias): metric_name
    for metric_name in ALLOWED_METRICS
    for alias in (metric_name, *METRIC_ALIASES[metric_name])
}


def resolve_metric_name(concept: str) -> str | None:
    """Resolve an exact financial concept alias to a canonical XBRL metric name."""

    return _METRIC_BY_NORMALIZED_CONCEPT.get(_normalize(concept))
