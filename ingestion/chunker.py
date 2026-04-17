"""
Chunking pipeline — splits Documents into Chunks using per-source strategies.

Strategies:
  bcc_minutes  → split by agenda item number (e.g. "4A", "7.", "Item 3B")
  ldc          → split by section/subsection heading
  civicclerk   → split by PDF heading boundaries (##-style or ALL-CAPS lines)
  gis          → single chunk per document
"""
from __future__ import annotations

import re
from typing import Callable

from ingestion.models import Chunk, Document

# Minimum characters per chunk — discard tiny fragments
MIN_CHUNK_CHARS = 80
# Maximum characters per chunk before hard-splitting
MAX_CHUNK_CHARS = 4000


def chunk_document(doc: Document) -> list[Chunk]:
    """Route to the correct per-source chunking strategy."""
    router: dict[str, Callable[[Document], list[str]]] = {
        "bcc_minutes": _chunk_bcc_minutes,
        "ldc": _chunk_ldc,
        "civicclerk": _chunk_civicclerk,
        "gis": _chunk_gis,
    }
    splitter = router.get(doc.source, _chunk_generic)
    raw_texts = splitter(doc)

    chunks: list[Chunk] = []
    for i, text in enumerate(raw_texts):
        text = text.strip()
        if len(text) < MIN_CHUNK_CHARS:
            continue
        chunks.append(
            Chunk(
                id=f"{doc.id}_chunk_{i}",
                document_id=doc.id,
                source=doc.source,
                doc_type=doc.doc_type,
                chunk_index=i,
                text=text,
                date=doc.date,
                url=doc.url,
                metadata=doc.metadata,
            )
        )
    return chunks


# ── Per-source splitters ───────────────────────────────────────────────────────

# Matches agenda item markers: "4A", "4A.", "Item 4A", "ITEM 4A.", leading "4."
_AGENDA_ITEM_RE = re.compile(
    r"(?m)^(?:ITEM\s+|Item\s+)?(\d{1,2}[A-Z]?\.?\s+[A-Z][^\n]{5,}|[A-Z]\.\s+[A-Z][^\n]{5,})"
)
_ITEM_HEADER_RE = re.compile(
    r"(?m)^(?:(?:ITEM|Item)\s+)?\d{1,2}[A-Z]?\s*[\.\-\)]\s*(?=[A-Z\d])"
)


def _chunk_bcc_minutes(doc: Document) -> list[str]:
    """Split BCC minutes by agenda item number. Never split mid-item."""
    text = doc.body
    # Find all agenda item boundary positions
    splits = [m.start() for m in _ITEM_HEADER_RE.finditer(text)]

    if len(splits) < 2:
        # Fallback: split on blank-line-separated paragraphs
        return _split_paragraphs(text)

    chunks: list[str] = []
    for i, start in enumerate(splits):
        end = splits[i + 1] if i + 1 < len(splits) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            chunks.extend(_hard_split(chunk))
    # Prepend any content before the first agenda item (preamble)
    if splits[0] > 200:
        preamble = text[: splits[0]].strip()
        if preamble:
            chunks.insert(0, preamble)
    return chunks


_LDC_SECTION_RE = re.compile(
    r"(?m)^(?:Section|SECTION|Sec\.)\s+\d+[\.\-]\d+(?:[\.\-]\d+)?"
    r"|^ARTICLE\s+[IVXLCDM\d]+"
    r"|^\d+[\.\-]\d+(?:[\.\-]\d+)?\s+[A-Z]"
)


def _chunk_ldc(doc: Document) -> list[str]:
    """Split LDC text by section/subsection heading, keeping section number in each chunk."""
    text = doc.body
    splits = [m.start() for m in _LDC_SECTION_RE.finditer(text)]

    if len(splits) < 2:
        return _split_paragraphs(text)

    chunks: list[str] = []
    for i, start in enumerate(splits):
        end = splits[i + 1] if i + 1 < len(splits) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            chunks.extend(_hard_split(chunk))
    return chunks


_HEADING_RE = re.compile(
    r"(?m)^(?:[A-Z][A-Z\s\-]{5,}|#{1,3}\s+.+)$"
)


def _chunk_civicclerk(doc: Document) -> list[str]:
    """Split CivicClerk staff reports by heading boundaries."""
    text = doc.body
    splits = [m.start() for m in _HEADING_RE.finditer(text)]

    if len(splits) < 2:
        return _split_paragraphs(text)

    chunks: list[str] = []
    for i, start in enumerate(splits):
        end = splits[i + 1] if i + 1 < len(splits) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            chunks.extend(_hard_split(chunk))
    return chunks


def _chunk_gis(doc: Document) -> list[str]:
    """GIS records are already atomic — return as a single chunk."""
    return [doc.body]


def _chunk_generic(doc: Document) -> list[str]:
    return _split_paragraphs(doc.body)


# ── Utilities ─────────────────────────────────────────────────────────────────

def _split_paragraphs(text: str) -> list[str]:
    """Split on blank lines; hard-split oversized paragraphs."""
    paras = re.split(r"\n{2,}", text)
    result: list[str] = []
    for p in paras:
        result.extend(_hard_split(p.strip()))
    return result


def _hard_split(text: str) -> list[str]:
    """If a chunk exceeds MAX_CHUNK_CHARS, split at sentence boundaries."""
    if len(text) <= MAX_CHUNK_CHARS:
        return [text]
    # Split on sentence boundaries
    sentences = re.split(r"(?<=[.!?])\s+", text)
    parts: list[str] = []
    current = ""
    for s in sentences:
        if len(current) + len(s) + 1 > MAX_CHUNK_CHARS and current:
            parts.append(current.strip())
            current = s
        else:
            current = (current + " " + s).strip()
    if current:
        parts.append(current)
    return parts
