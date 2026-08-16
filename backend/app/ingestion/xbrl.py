from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

SUPPORTED_FACT_FORMS = frozenset({"10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "8-K/A"})

# Different issuers and filing generations may use different US-GAAP concepts
# for the same normalized metric. Preserve xbrl_tag so the mapping remains auditable.
METRIC_NAME_BY_TAG = {
    "AccountsReceivableNetCurrent": "Accounts Receivable",
    "AccountsReceivableNet": "Accounts Receivable",
    "Assets": "Assets",
    "CashAndCashEquivalentsAtCarryingValue": "Cash and Cash Equivalents",
    "EarningsPerShareDiluted": "Diluted EPS",
    "GrossProfit": "Gross Profit",
    "Liabilities": "Liabilities",
    "NetCashProvidedByUsedInOperatingActivities": "Operating Cash Flow",
    "NetIncomeLoss": "Net Income",
    "OperatingIncomeLoss": "Operating Income",
    "PaymentsToAcquirePropertyPlantAndEquipment": "Capital Expenditures",
    "ResearchAndDevelopmentExpense": "Research and Development Expense",
    "RevenueFromContractWithCustomerExcludingAssessedTax": "Revenue",
    "Revenues": "Revenue",
    "SalesRevenueNet": "Revenue",
    "StockholdersEquity": "Stockholders' Equity",
}


@dataclass(frozen=True)
class NormalizedFinancialFact:
    metric_name: str
    taxonomy: str
    xbrl_tag: str
    value: Decimal
    unit: str
    period_start: date | None
    period_end: date
    filing_date: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession_number: str
    frame: str | None

    @property
    def identity(self) -> tuple[str, str, str, date | None, date, str]:
        return (
            self.taxonomy,
            self.xbrl_tag,
            self.unit,
            self.period_start,
            self.period_end,
            self.accession_number,
        )


def _optional_date(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def _optional_year(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_company_facts(payload: dict[str, Any]) -> list[NormalizedFinancialFact]:
    normalized: dict[
        tuple[str, str, str, date | None, date, str], NormalizedFinancialFact
    ] = {}

    for taxonomy, concepts in payload.get("facts", {}).items():
        if taxonomy != "us-gaap" or not isinstance(concepts, dict):
            continue
        for xbrl_tag, concept in concepts.items():
            metric_name = METRIC_NAME_BY_TAG.get(xbrl_tag)
            if metric_name is None or not isinstance(concept, dict):
                continue
            for unit, facts in concept.get("units", {}).items():
                if not isinstance(facts, list):
                    continue
                for fact in facts:
                    if not isinstance(fact, dict) or fact.get("form") not in SUPPORTED_FACT_FORMS:
                        continue
                    try:
                        value = Decimal(str(fact["val"]))
                        period_end = date.fromisoformat(fact["end"])
                        filing_date = date.fromisoformat(fact["filed"])
                        accession_number = str(fact["accn"])
                    except (InvalidOperation, KeyError, TypeError, ValueError):
                        continue

                    normalized_fact = NormalizedFinancialFact(
                        metric_name=metric_name,
                        taxonomy=taxonomy,
                        xbrl_tag=xbrl_tag,
                        value=value,
                        unit=str(unit),
                        period_start=_optional_date(fact.get("start")),
                        period_end=period_end,
                        filing_date=filing_date,
                        fiscal_year=_optional_year(fact.get("fy")),
                        fiscal_period=str(fact["fp"]) if fact.get("fp") else None,
                        form=str(fact["form"]),
                        accession_number=accession_number,
                        frame=str(fact["frame"]) if fact.get("frame") else None,
                    )
                    normalized[normalized_fact.identity] = normalized_fact

    return list(normalized.values())
