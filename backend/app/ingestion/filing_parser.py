import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString


@dataclass(frozen=True)
class ParsedSection:
    name: str
    content: str


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
ITEM_HEADING_PATTERN = re.compile(
    r"(?im)^(?:part\s+(?:i|ii)\s+)?item\s+(1a|1|2|7a|7|8)\s*[.\-:]?\s*[^\n]{0,160}$"
)
SECTION_MAP = {
    "10-K": (
        ("1", "Business"),
        ("1a", "Risk Factors"),
        ("7", "Management's Discussion and Analysis"),
        ("8", "Financial Statements"),
    ),
    "10-Q": (
        ("1", "Financial Statements"),
        ("2", "Management's Discussion and Analysis"),
        ("1a", "Risk Factors"),
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
    expected_sections = SECTION_MAP.get(form)
    if expected_sections is None:
        return [ParsedSection(name="Full Filing", content=text)] if text else []

    matches = list(ITEM_HEADING_PATTERN.finditer(text))
    candidates: dict[str, list[str]] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        content = text[match.start() : end].strip()
        if len(content) >= 1_000:
            candidates.setdefault(match.group(1).lower(), []).append(content)

    sections = []
    for item_number, section_name in expected_sections:
        item_candidates = candidates.get(item_number, [])
        if item_candidates:
            sections.append(ParsedSection(name=section_name, content=max(item_candidates, key=len)))

    return sections or ([ParsedSection(name="Full Filing", content=text)] if text else [])


def parse_filing_html(html: str, form: str) -> list[ParsedSection]:
    return extract_sections(html_to_text(html), form)


def chunk_text(text: str, max_characters: int = 6_000, overlap: int = 600) -> list[str]:
    if max_characters <= overlap:
        raise ValueError("max_characters must be greater than overlap")

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_characters, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + max_characters // 2, end)
            if boundary == -1:
                boundary = text.rfind(" ", start + max_characters // 2, end)
            if boundary != -1:
                end = boundary

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)

    return chunks
