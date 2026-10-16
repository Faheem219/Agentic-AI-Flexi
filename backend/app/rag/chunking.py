"""Recursive text chunking with overlap (paragraph -> sentence -> word boundaries)."""

from __future__ import annotations

import re

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])")


def _split(text: str, size: int) -> list[str]:
    """Split text into pieces no longer than `size`, preferring natural boundaries."""
    if len(text) <= size:
        return [text]
    for splitter in (lambda t: re.split(r"\n\s*\n", t), lambda t: _SENT.split(t), lambda t: t.split(" ")):
        parts = [p.strip() for p in splitter(text) if p.strip()]
        if len(parts) > 1:
            out: list[str] = []
            for p in parts:
                out.extend(_split(p, size) if len(p) > size else [p])
            return out
    return [text[i : i + size] for i in range(0, len(text), size)]


def chunk_text(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    """Greedy-pack natural pieces into chunks of ~`size` chars, carrying `overlap` chars of
    context from the previous chunk so facts that straddle a boundary stay retrievable."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    if not text:
        return []
    pieces = _split(text, size)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current} {piece}".strip() if current else piece
        if len(candidate) <= size:
            current = candidate
            continue
        if current:
            chunks.append(current)
            tail = current[-overlap:] if overlap else ""
            # start the overlap on a word boundary
            tail = tail[tail.find(" ") + 1 :] if " " in tail else tail
            current = f"{tail} {piece}".strip() if tail else piece
            if len(current) > size + overlap:
                current = piece
        else:
            current = piece
    if current:
        chunks.append(current)
    return chunks


def chunk_pages(pages: list[str], size: int = 900, overlap: int = 150) -> list[tuple[int, str]]:
    """Chunk page by page so each chunk keeps an accurate page number for citations."""
    out: list[tuple[int, str]] = []
    for page_no, page_text in enumerate(pages, start=1):
        for chunk in chunk_text(page_text, size, overlap):
            if len(chunk) >= 40:  # skip page furniture (headers, page numbers)
                out.append((page_no, chunk))
    return out
