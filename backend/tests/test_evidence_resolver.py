from app.research.evidence import resolve_metric_name


def test_resolve_metric_name_accepts_canonical_names_and_aliases() -> None:
    assert resolve_metric_name("Accounts Receivable") == "Accounts Receivable"
    assert resolve_metric_name("trade receivables") == "Accounts Receivable"
    assert resolve_metric_name("cash from operations") == "Operating Cash Flow"


def test_resolve_metric_name_leaves_company_specific_concepts_for_filing_search() -> None:
    assert resolve_metric_name("Data Center revenue") is None
    assert resolve_metric_name("customer concentration") is None
