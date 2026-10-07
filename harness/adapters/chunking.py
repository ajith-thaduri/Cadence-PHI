"""Word / token windowing helpers shared by adapters."""
from __future__ import annotations

import re

# Same pattern as gliner.data_processing.tokenizer.WhitespaceTokenSplitter and the
# LOGICAL tool's splitter.
WORD_RE = re.compile(r"\w+(?:[-_]\w+)*|\S")


def word_spans(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in WORD_RE.finditer(text)]


def word_windows(n_words: int, size: int, overlap: int) -> list[tuple[int, int]]:
    """[start, end) word-index windows of `size` with `overlap` (LOGICAL tool semantics)."""
    if n_words <= size:
        return [(0, n_words)]
    out, start = [], 0
    while start < n_words:
        end = min(start + size, n_words)
        out.append((start, end))
        if end >= n_words:
            break
        start = max(start + 1, end - overlap)
    return out
