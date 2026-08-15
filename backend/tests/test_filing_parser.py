from functools import partial

import pytest

from app.ingestion.filing_parser import chunk_text, count_tokens, parse_filing_html

openai_token_counter = partial(
    count_tokens, model="text-embedding-3-small", provider="openai"
)


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


def test_item_15_is_not_mistaken_for_item_1() -> None:
    business = "Actual business information. " * 50
    exhibits = "Exhibit and financial schedule information. " * 80
    html = f"""
    <html><body>
      <h2>Part I</h2>
      <h2>Item 1. Business</h2><p>{business}</p>
      <h2>Item 1A. Risk Factors</h2><p>{business}</p>
      <h2>Part IV</h2>
      <h2>Item 15. Exhibits and Financial Statement Schedules</h2><p>{exhibits}</p>
    </body></html>
    """

    sections = parse_filing_html(html, "10-K")

    business_section = next(section for section in sections if section.name == "Business")
    assert business_section.content.startswith("Item 1. Business")
    assert "Item 15" not in business_section.content


def test_10q_uses_part_context_for_repeated_item_numbers() -> None:
    financials = "Quarterly financial statement content. " * 50
    management = "Management discussion content. " * 50
    legal = "Legal proceeding content that must not become financial statements. " * 80
    risks = "Quarterly risk factor content. " * 50
    html = f"""
    <html><body>
      <h2>Part I. Financial Information</h2>
      <h2>Item 1. Financial Statements</h2><p>{financials}</p>
      <h2>Item 2. Management's Discussion and Analysis</h2><p>{management}</p>
      <h2>Item 3. Market Risk</h2><p>{management}</p>
      <h2>Part II. Other Information</h2>
      <h2>Item 1. Legal Proceedings</h2><p>{legal}</p>
      <h2>Item 1A. Risk Factors</h2><p>{risks}</p>
      <h2>Item 2. Unregistered Sales</h2><p>{legal}</p>
    </body></html>
    """

    sections = parse_filing_html(html, "10-Q")

    assert sections[0].content.startswith("Item 1. Financial Statements")
    assert "Legal proceeding" not in sections[0].content
    assert sections[1].content.startswith("Item 2. Management's Discussion")
    assert "Item 3. Market Risk" not in sections[1].content
    assert sections[2].content.startswith("Item 1A. Risk Factors")


def test_10k_financial_statements_can_come_from_item_15() -> None:
    cross_reference = "The required information is included under Item 15."
    statements = "Consolidated financial statement and note content. " * 50
    html = f"""
    <html><body>
      <h2>Part II</h2>
      <h2>Item 8. Financial Statements and Supplementary Data</h2>
      <p>{cross_reference}</p>
      <h2>Item 9. Changes and Disagreements</h2>
      <h2>Part IV</h2>
      <h2>Item 15. Exhibits and Financial Statement Schedules</h2><p>{statements}</p>
      <h2>Item 16. Form 10-K Summary</h2>
    </body></html>
    """

    sections = parse_filing_html(html, "10-K")

    financial_section = next(
        section for section in sections if section.name == "Financial Statements"
    )
    assert financial_section.content.startswith("Item 15. Exhibits")
    assert "Consolidated financial statement" in financial_section.content


def test_chunk_text_creates_sentence_aligned_bounded_chunks() -> None:
    sentences = [f"Revenue sentence {index} contains useful context." for index in range(40)]
    text = " ".join(sentences)

    chunks = chunk_text(
        text, max_tokens=50, overlap_tokens=10, token_counter=openai_token_counter
    )

    assert len(chunks) > 1
    assert all(openai_token_counter(chunk) <= 50 for chunk in chunks)
    assert all(chunk.endswith(".") for chunk in chunks)


def test_chunk_text_preserves_paragraph_boundaries() -> None:
    text = "First sentence. Second sentence.\n\nA separate paragraph starts here."

    chunks = chunk_text(
        text, max_tokens=100, overlap_tokens=10, token_counter=openai_token_counter
    )

    assert chunks == [
        "First sentence. Second sentence.\n\nA separate paragraph starts here."
    ]


def test_chunk_text_repeats_complete_sentences_as_overlap() -> None:
    text = "First has four words. Second has four words. Third has four words."

    chunks = chunk_text(
        text, max_tokens=10, overlap_tokens=5, token_counter=openai_token_counter
    )

    assert chunks == [
        "First has four words. Second has four words.",
        "Second has four words. Third has four words.",
    ]


def test_chunk_text_uses_injected_model_token_counter() -> None:
    def one_token_per_word(text: str) -> int:
        return len(text.split())

    text = "One two three. Four five six. Seven eight nine."

    chunks = chunk_text(
        text,
        max_tokens=6,
        overlap_tokens=0,
        token_counter=one_token_per_word,
    )

    assert chunks == ["One two three. Four five six.", "Seven eight nine."]


@pytest.mark.parametrize(
    ("max_tokens", "overlap_tokens"),
    [(0, 0), (10, -1), (10, 10)],
)
def test_chunk_text_rejects_invalid_budgets(max_tokens: int, overlap_tokens: int) -> None:
    with pytest.raises(ValueError):
        chunk_text("Some filing text.", max_tokens, overlap_tokens)
