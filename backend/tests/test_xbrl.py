from datetime import date
from decimal import Decimal

from app.ingestion.xbrl import parse_company_facts


def test_parse_company_facts_normalizes_supported_us_gaap_metrics() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            {
                                "start": "2026-01-27",
                                "end": "2026-04-26",
                                "val": 44_062_000_000,
                                "accn": "0001045810-26-000052",
                                "fy": 2026,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2026-05-20",
                                "frame": "CY2026Q1",
                            }
                        ]
                    }
                },
                "UnsupportedConcept": {
                    "units": {
                        "USD": [
                            {
                                "end": "2026-04-26",
                                "val": 1,
                                "accn": "ignored",
                                "form": "10-Q",
                                "filed": "2026-05-20",
                            }
                        ]
                    }
                },
            },
            "dei": {
                "EntityPublicFloat": {
                    "units": {
                        "USD": [
                            {
                                "end": "2026-04-26",
                                "val": 1,
                                "accn": "ignored",
                                "form": "10-Q",
                                "filed": "2026-05-20",
                            }
                        ]
                    }
                }
            },
        }
    }

    facts = parse_company_facts(payload)

    assert len(facts) == 1
    fact = facts[0]
    assert fact.metric_name == "Revenue"
    assert fact.xbrl_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert fact.value == Decimal(44062000000)
    assert fact.period_start == date(2026, 1, 27)
    assert fact.period_end == date(2026, 4, 26)
    assert fact.fiscal_year == 2026
    assert fact.fiscal_period == "Q1"


def test_parse_company_facts_preserves_instant_facts_and_amendments() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "Assets": {
                    "units": {
                        "USD": [
                            {
                                "end": "2025-12-31",
                                "val": "125.50",
                                "accn": "0000000000-26-000001",
                                "fy": "2025",
                                "fp": "FY",
                                "form": "10-K/A",
                                "filed": "2026-03-01",
                            }
                        ]
                    }
                }
            }
        }
    }

    facts = parse_company_facts(payload)

    assert len(facts) == 1
    assert facts[0].period_start is None
    assert facts[0].value == Decimal("125.50")
    assert facts[0].form == "10-K/A"


def test_parse_company_facts_normalizes_accounts_receivable_aliases() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "AccountsReceivableNetCurrent": {
                    "units": {
                        "USD": [
                            {
                                "end": "2026-04-26",
                                "val": 23_065_000_000,
                                "accn": "0001045810-26-000052",
                                "fy": 2027,
                                "fp": "Q1",
                                "form": "10-Q",
                                "filed": "2026-05-20",
                            }
                        ]
                    }
                }
            }
        }
    }

    facts = parse_company_facts(payload)

    assert len(facts) == 1
    assert facts[0].metric_name == "Accounts Receivable"
    assert facts[0].period_start is None


def test_parse_company_facts_keeps_distinct_quarter_and_year_to_date_facts() -> None:
    common = {
        "end": "2026-06-30",
        "accn": "0000000000-26-000002",
        "fy": 2026,
        "fp": "Q2",
        "form": "10-Q",
        "filed": "2026-08-01",
    }
    payload = {
        "facts": {
            "us-gaap": {
                "NetIncomeLoss": {
                    "units": {
                        "USD": [
                            {**common, "start": "2026-04-01", "val": 30},
                            {**common, "start": "2026-01-01", "val": 50},
                            {**common, "start": "2026-04-01", "val": 30},
                        ]
                    }
                }
            }
        }
    }

    facts = parse_company_facts(payload)

    assert len(facts) == 2
    assert {fact.period_start for fact in facts} == {
        date(2026, 1, 1),
        date(2026, 4, 1),
    }


def test_parse_company_facts_skips_malformed_and_unsupported_form_facts() -> None:
    payload = {
        "facts": {
            "us-gaap": {
                "GrossProfit": {
                    "units": {
                        "USD": [
                            {
                                "end": "not-a-date",
                                "val": 10,
                                "accn": "bad",
                                "form": "10-Q",
                                "filed": "2026-05-20",
                            },
                            {
                                "end": "2026-04-26",
                                "val": 10,
                                "accn": "ownership",
                                "form": "4",
                                "filed": "2026-05-20",
                            },
                        ]
                    }
                }
            }
        }
    }

    assert parse_company_facts(payload) == []
