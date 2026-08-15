from app.ingestion.filing_parser import chunk_text, parse_filing_html


def test_parse_filing_html_extracts_named_10k_sections() -> None:
    long_content = "Material business information. " * 60
    html = f"""
    <html><body>
      <h2>Item 1. Business</h2><p>{long_content}</p>
      <h2>Item 1A. Risk Factors</h2><p>{long_content}</p>
      <h2>Item 7. Management's Discussion and Analysis</h2><p>{long_content}</p>
      <h2>Item 8. Financial Statements</h2><p>{long_content}</p>
    </body></html>
    """

    sections = parse_filing_html(html, "10-K")

    assert [section.name for section in sections] == [
        "Business",
        "Risk Factors",
        "Management's Discussion and Analysis",
        "Financial Statements",
    ]
    assert all("Material business information" in section.content for section in sections)


def test_parse_8k_falls_back_to_full_filing() -> None:
    sections = parse_filing_html("<html><body><p>Current event details.</p></body></html>", "8-K")

    assert len(sections) == 1
    assert sections[0].name == "Full Filing"
    assert sections[0].content == "Current event details."


def test_chunk_text_creates_overlapping_bounded_chunks() -> None:
    text = " ".join(f"word-{index}" for index in range(500))

    chunks = chunk_text(text, max_characters=500, overlap=50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 500 for chunk in chunks)
