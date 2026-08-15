from dataclasses import dataclass
from datetime import date
from typing import Any, Self
from urllib.parse import urlparse

import httpx

from app.core.config import settings

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
SUPPORTED_FORMS = frozenset({"10-K", "10-Q", "8-K"})
MAX_DOCUMENT_BYTES = 50 * 1024 * 1024


class SECClientConfigurationError(ValueError):
    pass


class SECDocumentError(ValueError):
    pass


@dataclass(frozen=True)
class SECFilingMetadata:
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    document_url: str


def _optional_date(value: str) -> date | None:
    return date.fromisoformat(value) if value else None


def parse_recent_filings(payload: dict[str, Any], cik: str) -> list[SECFilingMetadata]:
    recent = payload.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber", [])
    cik_without_padding = str(int(cik))
    filings: list[SECFilingMetadata] = []

    for index, accession_number in enumerate(accessions):
        form = recent["form"][index]
        primary_document = recent["primaryDocument"][index]
        if form not in SUPPORTED_FORMS or not primary_document:
            continue

        accession_path = accession_number.replace("-", "")
        filings.append(
            SECFilingMetadata(
                accession_number=accession_number,
                form=form,
                filing_date=date.fromisoformat(recent["filingDate"][index]),
                report_date=_optional_date(recent["reportDate"][index]),
                primary_document=primary_document,
                document_url=(
                    f"{SEC_ARCHIVES_URL}/{cik_without_padding}/{accession_path}/{primary_document}"
                ),
            )
        )

    return filings


class SECClient:
    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        user_agent = settings.sec_user_agent.strip()
        if "@" not in user_agent or "your-email" in user_agent.lower():
            raise SECClientConfigurationError(
                "SEC_USER_AGENT must identify the app and include a real contact email"
            )

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=30.0,
            follow_redirects=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_recent_filings(self, cik: str) -> list[SECFilingMetadata]:
        response = await self._client.get(f"{SEC_SUBMISSIONS_URL}/CIK{cik}.json")
        response.raise_for_status()
        return parse_recent_filings(response.json(), cik)

    async def get_filing_document(self, document_url: str) -> str:
        hostname = urlparse(document_url).hostname
        if hostname is None or (hostname != "sec.gov" and not hostname.endswith(".sec.gov")):
            raise SECDocumentError("Filing document URL must use an SEC.gov host")

        response = await self._client.get(
            document_url,
            headers={"Accept": "text/html,application/xhtml+xml"},
        )
        response.raise_for_status()
        if len(response.content) > MAX_DOCUMENT_BYTES:
            raise SECDocumentError("Filing document exceeds the 50 MB ingestion limit")
        return response.text
