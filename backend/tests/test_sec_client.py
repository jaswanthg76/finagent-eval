from app.ingestion.sec_client import parse_recent_filings


def test_parse_recent_filings_filters_forms_and_builds_archive_url() -> None:
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0001045810-26-000001", "0001045810-26-000002"],
                "filingDate": ["2026-05-20", "2026-05-21"],
                "reportDate": ["2026-04-26", ""],
                "form": ["10-Q", "4"],
                "primaryDocument": ["nvda-20260426.htm", "ownership.xml"],
            }
        }
    }

    filings = parse_recent_filings(payload, "0001045810")

    assert len(filings) == 1
    assert filings[0].accession_number == "0001045810-26-000001"
    assert filings[0].form == "10-Q"
    assert filings[0].report_date is not None
    assert filings[0].document_url == (
        "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000001/nvda-20260426.htm"
    )
