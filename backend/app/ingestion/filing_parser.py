import re
from dataclasses import dataclass
from functools import cache
from itertools import pairwise
from typing import Protocol

import tiktoken
from bs4 import BeautifulSoup, NavigableString


@dataclass(frozen=True)
class ParsedSection:
    name: str
    content: str


class TokenCounter(Protocol):
    """Provider-neutral interface for an embedding model's tokenizer."""

    def __call__(self, text: str) -> int: ...


@dataclass(frozen=True)
class _TextUnit:
    text: str
    paragraph_index: int


BLOCK_TAGS = (
    "address",
    "article",
    "blockquote",
    "br",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "p",
    "section",
    "table",
    "tr",
)
FILING_HEADING_PATTERN = re.compile(
    r"(?im)^(?:"
    r"part\s+(?P<part>iv|iii|ii|i)\b[^\n]{0,160}"
    r"|"
    r"item\s+(?P<item>\d{1,2}[a-z]?)\b\s*[.\-:]?\s*[^\n]{0,160}"
    r")$"
)
SENTENCE_BOUNDARY_PATTERN = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\[])")
SECTION_MAP = {
    "10-K": (
        ("Business", (("i", "1"),)),
        ("Risk Factors", (("i", "1a"),)),
        ("Management's Discussion and Analysis", (("ii", "7"),)),
        # Some issuers, including NVIDIA, place the statements under Item 15
        # and leave only a cross-reference in Item 8.
        ("Financial Statements", (("ii", "8"), ("iv", "15"))),
    ),
    "10-Q": (
        ("Financial Statements", (("i", "1"),)),
        ("Management's Discussion and Analysis", (("i", "2"),)),
        ("Risk Factors", (("ii", "1a"),)),
    ),
}


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup.find_all(["script", "style", "noscript", "svg", "ix:header"]):
        element.decompose()

    for element in soup.find_all(BLOCK_TAGS):
        element.insert_before(NavigableString("\n"))
        element.insert_after(NavigableString("\n"))

    raw_text = soup.get_text(" ")
    lines = []
    for line in raw_text.splitlines():
        normalized = re.sub(r"[ \t\xa0]+", " ", line).strip()
        if normalized:
            lines.append(normalized)
    return "\n".join(lines)


def extract_sections(text: str, form: str) -> list[ParsedSection]:
    section_definitions = SECTION_MAP.get(form)
    if section_definitions is None:
        return [ParsedSection(name="Full Filing", content=text)] if text else []

    matches = list(FILING_HEADING_PATTERN.finditer(text))
    candidates: dict[tuple[str | None, str], list[str]] = {}
    current_part: str | None = None
    for index, match in enumerate(matches):
        part_number = match.group("part")
        if part_number is not None:
            current_part = part_number.lower()
            continue

        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.start() : end].strip()
        if len(content) >= 1_000:
            item_number = match.group("item").lower()
            candidates.setdefault((current_part, item_number), []).append(content)

    sections = []
    for section_name, source_items in section_definitions:
        section_candidates: list[str] = []
        for part_number, item_number in source_items:
            section_candidates.extend(candidates.get((part_number, item_number), []))
            # Some older or synthetic filings omit explicit Part headings.
            section_candidates.extend(candidates.get((None, item_number), []))
        if section_candidates:
            sections.append(
                ParsedSection(name=section_name, content=max(section_candidates, key=len))
            )

    return sections or ([ParsedSection(name="Full Filing", content=text)] if text else [])


def parse_filing_html(html: str, form: str) -> list[ParsedSection]:
    return extract_sections(html_to_text(html), form)


@cache
def _tokenizer(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str, model: str = "text-embedding-3-small") -> int:
    """Count tokens with the selected embedding model's tokenizer."""

    return len(_tokenizer(model).encode(text, disallowed_special=()))


def _split_oversized_text(
    text: str, max_tokens: int, token_counter: TokenCounter
) -> list[str]:
    """Split an unusually long sentence while preferring word boundaries."""

    pieces: list[str] = []
    current_words: list[str] = []
    for word in text.split():
        candidate = " ".join((*current_words, word))
        if current_words and token_counter(candidate) > max_tokens:
            pieces.append(" ".join(current_words))
            current_words = [word]
        else:
            current_words.append(word)

    if current_words:
        pieces.append(" ".join(current_words))
    return pieces


def _text_units(text: str, max_tokens: int, token_counter: TokenCounter) -> list[_TextUnit]:
    units: list[_TextUnit] = []
    paragraphs = [line.strip() for line in re.split(r"\n+", text) if line.strip()]
    for paragraph_index, paragraph in enumerate(paragraphs):
        sentences = [
            sentence.strip()
            for sentence in SENTENCE_BOUNDARY_PATTERN.split(paragraph)
            if sentence.strip()
        ]
        for sentence in sentences:
            pieces = (
                [sentence]
                if token_counter(sentence) <= max_tokens
                else _split_oversized_text(sentence, max_tokens, token_counter)
            )
            units.extend(_TextUnit(piece, paragraph_index) for piece in pieces if piece)
    return units


def _render_units(units: list[_TextUnit]) -> str:
    if not units:
        return ""

    parts = [units[0].text]
    for previous, current in pairwise(units):
        separator = "\n\n" if previous.paragraph_index != current.paragraph_index else " "
        parts.extend((separator, current.text))
    return "".join(parts)


def _overlap_units(
    units: list[_TextUnit], overlap_tokens: int, token_counter: TokenCounter
) -> list[_TextUnit]:
    overlap: list[_TextUnit] = []
    for unit in reversed(units):
        candidate = [unit, *overlap]
        if token_counter(_render_units(candidate)) > overlap_tokens:
            break
        overlap = candidate
    return overlap


def chunk_text(
    text: str,
    max_tokens: int = 900,
    overlap_tokens: int = 100,
    token_counter: TokenCounter = count_tokens,
) -> list[str]:
    """Create paragraph-aware, sentence-aligned chunks within a token budget."""

    if max_tokens <= 0:
        raise ValueError("max_tokens must be greater than zero")
    if overlap_tokens < 0:
        raise ValueError("overlap_tokens cannot be negative")
    if max_tokens <= overlap_tokens:
        raise ValueError("max_tokens must be greater than overlap_tokens")

    units = _text_units(text, max_tokens, token_counter)
    if not units:
        return []

    chunks: list[str] = []
    current: list[_TextUnit] = []
    for unit in units:
        candidate = [*current, unit]
        if current and token_counter(_render_units(candidate)) > max_tokens:
            chunks.append(_render_units(current))
            current = _overlap_units(current, overlap_tokens, token_counter)
            while current and token_counter(_render_units([*current, unit])) > max_tokens:
                current.pop(0)
        current.append(unit)

    if current:
        chunks.append(_render_units(current))
    return chunks
