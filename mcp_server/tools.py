"""
MCP tool implementations — thin wrappers around the retrieval pipeline.
"""
from __future__ import annotations

import dataclasses
import json
from typing import Optional

from retrieval.pipeline import RetrievedChunk, query as rag_query
from storage.db import get_db


def search_pasco_records(
    query: str,
    doc_type: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """Core hybrid search with optional filters. Returns list of retrieved chunks."""
    result = rag_query(
        question=query,
        doc_type=doc_type,
        date_from=date_from,
        date_to=date_to,
        top_k=5,
    )
    return [dataclasses.asdict(s) for s in result.sources]


def get_zoning_rules(zoning_code: str) -> dict:
    """Direct lookup: return details for a zoning code (e.g. 'MPUD', 'R2')."""
    db = get_db()
    rows = db.execute(
        "SELECT id, title, body, date, url FROM documents "
        "WHERE source = 'gis' AND (body LIKE ? OR title LIKE ?) LIMIT 1",
        [f"%{zoning_code}%", f"%{zoning_code}%"],
    ).fetchall()
    if not rows:
        # Fall back to LDC search
        result = rag_query(
            question=f"What does the {zoning_code} zoning code allow?",
            doc_type="ordinance",
            top_k=3,
        )
        return {
            "zoning_code": zoning_code,
            "answer": result.answer,
            "sources": [dataclasses.asdict(s) for s in result.sources],
        }
    row = rows[0]
    return {
        "zoning_code": zoning_code,
        "document_id": row[0],
        "title": row[1],
        "body": row[2][:2000],
        "date": row[3],
        "url": row[4],
    }


def get_meeting_item(meeting_date: str, item_number: int) -> dict:
    """Retrieve a specific agenda item and its vote result."""
    db = get_db()
    rows = db.execute(
        "SELECT id, title, body, url FROM documents "
        "WHERE source = 'bcc_minutes' AND date = ? LIMIT 1",
        [meeting_date],
    ).fetchall()
    if not rows:
        return {"error": f"No meeting found for date {meeting_date}"}

    doc_id, title, body, url = rows[0]
    # Find the specific agenda item in the body
    import re
    pattern = re.compile(
        rf"(?i)(?:item|ITEM)\s+{item_number}[A-Z]?\b.*?(?=(?:item|ITEM)\s+\d|$)",
        re.DOTALL,
    )
    m = pattern.search(body)
    item_text = m.group(0)[:2000] if m else body[:2000]

    return {
        "meeting_date": meeting_date,
        "item_number": item_number,
        "document_id": doc_id,
        "title": title,
        "item_text": item_text,
        "url": url,
    }


def find_commissioner_votes(
    topic: str,
    commissioner: Optional[str] = None,
) -> list[dict]:
    """Find vote records related to a topic, optionally filtered by commissioner."""
    q = f"{commissioner} vote on {topic}" if commissioner else f"vote on {topic}"
    result = rag_query(
        question=q,
        doc_type="meeting_minutes",
        top_k=5,
    )
    votes = []
    for s in result.sources:
        # Extract vote lines from chunk text
        import re
        vote_lines = re.findall(
            r"(?:motion|vote|aye|nay|approved|denied|passed|failed)[^\n]*",
            s.text,
            re.IGNORECASE,
        )
        votes.append({
            "chunk_id": s.chunk_id,
            "date": s.date,
            "url": s.url,
            "vote_lines": vote_lines[:5],
            "text_snippet": s.text[:400],
        })
    return votes


def summarize_topic(topic: str, date_range: Optional[str] = None) -> dict:
    """Summarize county decisions on a topic across multiple documents."""
    date_from = date_to = None
    if date_range:
        import re
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", date_range)
        if len(dates) >= 2:
            date_from, date_to = dates[0], dates[1]
        elif len(dates) == 1:
            date_from = dates[0]

    result = rag_query(
        question=f"What has Pasco County decided about {topic}?",
        date_from=date_from,
        date_to=date_to,
        top_k=5,
    )
    return {
        "topic": topic,
        "date_range": date_range,
        "summary": result.answer,
        "sources": [dataclasses.asdict(s) for s in result.sources],
        "expanded_queries": result.expanded_queries,
    }
