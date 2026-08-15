import re

METRIC_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Revenue", ("revenue", "sales", "top line")),
    ("Gross Profit", ("gross profit",)),
    ("Operating Income", ("operating income", "operating profit")),
    ("Net Income", ("net income", "net earnings", "bottom line", "profit")),
    ("Diluted EPS", ("diluted eps", "eps", "earnings per share")),
    ("Cash and Cash Equivalents", ("cash balance", "cash position", "cash equivalents")),
    ("Operating Cash Flow", ("operating cash flow", "cash from operations")),
    ("Capital Expenditures", ("capital expenditures", "capital expenditure", "capex")),
    (
        "Research and Development Expense",
        ("research and development", "r d expense", "r d spending", "r&d"),
    ),
    ("Assets", ("assets", "total assets")),
    ("Liabilities", ("liabilities", "total liabilities")),
    ("Stockholders' Equity", ("stockholders equity", "shareholders equity", "book value")),
)


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
