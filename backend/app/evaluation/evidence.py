import re

WORD_RE = re.compile(r"[a-z0-9]+")
STOP_WORDS = {
    "about",
    "after",
    "also",
    "because",
    "before",
    "being",
    "could",
    "from",
    "have",
    "into",
    "management",
    "their",
    "there",
    "these",
    "they",
    "this",
    "those",
    "through",
    "with",
    "would",
}


def _stem(word: str) -> str:
    for suffix in ("ments", "ment", "ing", "ed", "es", "s"):
        if len(word) - len(suffix) >= 4 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def relevant_excerpt(content: str, query: str, max_chars: int) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= max_chars:
        return normalized
    query_terms = {
        _stem(word)
        for word in WORD_RE.findall(query.lower())
        if len(word) >= 4 and word not in STOP_WORDS
    }
    if not query_terms:
        return normalized[:max_chars]

    content_terms = [_stem(word) for word in WORD_RE.findall(normalized.lower())]
    term_frequencies = {
        term: content_terms.count(term) for term in query_terms if term in content_terms
    }
    stride = max(1, max_chars // 2)
    starts = list(range(0, max(1, len(normalized) - max_chars + 1), stride))
    final_start = len(normalized) - max_chars
    if final_start not in starts:
        starts.append(final_start)

    def score(start: int) -> tuple[float, int]:
        window_terms = {
            _stem(word)
            for word in WORD_RE.findall(normalized[start : start + max_chars].lower())
        }
        relevance = sum(
            1.0 / term_frequencies[term]
            for term in query_terms & window_terms
            if term in term_frequencies
        )
        return relevance, -start

    best_start = max(starts, key=score)
    excerpt = normalized[best_start : best_start + max_chars]
    if best_start > 0:
        first_space = excerpt.find(" ")
        if first_space >= 0:
            excerpt = excerpt[first_space + 1 :]
    return excerpt
