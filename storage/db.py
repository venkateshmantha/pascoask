"""
SQLite storage layer using sqlite-utils.

Tables
------
documents   : raw normalized documents from all sources
chunks      : chunked + enriched pieces ready for embedding
embed_queue : tracks which chunks still need embedding
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import sqlite_utils

from config import settings

logger = logging.getLogger(__name__)


def get_db(path: Path | None = None) -> sqlite_utils.Database:
    """Return an open sqlite-utils Database, creating schema if needed."""
    db_path = path or settings.sqlite_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite_utils.Database(db_path)
    _ensure_schema(db)
    return db


def _ensure_schema(db: sqlite_utils.Database) -> None:
    """Idempotently create all tables and indexes."""

    # ── documents ────────────────────────────────────────────────────────
    if "documents" not in db.table_names():
        db["documents"].create(
            {
                "id": str,          # e.g. "bcc_minutes_2024_03_12"
                "source": str,      # bcc_minutes | ldc | civicclerk | gis
                "doc_type": str,    # meeting_minutes | ordinance | zoning_code | staff_report
                "title": str,
                "body": str,
                "date": str,        # ISO 8601 date string, may be empty
                "url": str,
                "metadata_json": str,  # JSON blob for extra fields
                "scraped_at": str,
            },
            pk="id",
        )
        db["documents"].create_index(["source"])
        db["documents"].create_index(["doc_type"])
        db["documents"].create_index(["date"])
        logger.info("Created 'documents' table")

    # ── chunks ────────────────────────────────────────────────────────────
    if "chunks" not in db.table_names():
        db["chunks"].create(
            {
                "id": str,              # "{doc_id}_chunk_{n}"
                "document_id": str,
                "source": str,
                "doc_type": str,
                "chunk_index": int,
                "text": str,            # raw chunk text
                "summary": str,         # LLM-generated plain-English summary
                "entity_tags": str,     # JSON list of extracted entities
                "citizen_question": str,# "question this chunk answers"
                "date": str,
                "url": str,
                "metadata_json": str,
                "enriched_at": str,     # null until enrichment runs
                "embedded_at": str,     # null until embedding runs
            },
            pk="id",
            foreign_keys=[("document_id", "documents", "id")],
        )
        db["chunks"].create_index(["document_id"])
        db["chunks"].create_index(["source"])
        db["chunks"].create_index(["embedded_at"])
        logger.info("Created 'chunks' table")

    # ── scrape_log ────────────────────────────────────────────────────────
    if "scrape_log" not in db.table_names():
        db["scrape_log"].create(
            {
                "url": str,
                "source": str,
                "status": str,      # success | error | skipped
                "scraped_at": str,
                "error": str,
            },
            pk="url",
        )
        logger.info("Created 'scrape_log' table")


# ── Convenience helpers ────────────────────────────────────────────────────────

def upsert_document(db: sqlite_utils.Database, doc: dict[str, Any]) -> None:
    """Insert or replace a document record."""
    doc.setdefault("scraped_at", datetime.utcnow().isoformat())
    if isinstance(doc.get("metadata_json"), dict):
        doc["metadata_json"] = json.dumps(doc["metadata_json"])
    db["documents"].upsert(doc, pk="id")


def upsert_chunk(db: sqlite_utils.Database, chunk: dict[str, Any]) -> None:
    """Insert or replace a chunk record."""
    if isinstance(chunk.get("metadata_json"), dict):
        chunk["metadata_json"] = json.dumps(chunk["metadata_json"])
    if isinstance(chunk.get("entity_tags"), list):
        chunk["entity_tags"] = json.dumps(chunk["entity_tags"])
    db["chunks"].upsert(chunk, pk="id")


def log_scrape(
    db: sqlite_utils.Database,
    url: str,
    source: str,
    status: str,
    error: str = "",
) -> None:
    db["scrape_log"].upsert(
        {
            "url": url,
            "source": source,
            "status": status,
            "scraped_at": datetime.utcnow().isoformat(),
            "error": error,
        },
        pk="url",
    )


def already_scraped(db: sqlite_utils.Database, url: str) -> bool:
    """Return True if this URL was previously scraped successfully."""
    row = db.execute(
        "SELECT status FROM scrape_log WHERE url = ?", [url]
    ).fetchone()
    return row is not None and row[0] == "success"


def document_exists(db: sqlite_utils.Database, doc_id: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM documents WHERE id = ?", [doc_id]
    ).fetchone()
    return row is not None


def get_unenriched_chunks(
    db: sqlite_utils.Database, limit: int = 100
) -> list[dict[str, Any]]:
    """Return chunks that have not yet been enriched by the LLM."""
    rows = db.execute(
        "SELECT * FROM chunks WHERE enriched_at IS NULL LIMIT ?", [limit]
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM chunks LIMIT 0").description]
    return [dict(zip(cols, row)) for row in rows]


def get_unembedded_chunks(
    db: sqlite_utils.Database, limit: int = 500
) -> list[dict[str, Any]]:
    """Return enriched chunks that have not yet been embedded."""
    rows = db.execute(
        "SELECT * FROM chunks WHERE enriched_at IS NOT NULL AND embedded_at IS NULL LIMIT ?",
        [limit],
    ).fetchall()
    cols = [d[0] for d in db.execute("SELECT * FROM chunks LIMIT 0").description]
    return [dict(zip(cols, row)) for row in rows]


def stats(db: sqlite_utils.Database) -> dict[str, Any]:
    """Return a quick summary of what's in the DB."""
    return {
        "documents": db.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
        "chunks": db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "enriched_chunks": db.execute(
            "SELECT COUNT(*) FROM chunks WHERE enriched_at IS NOT NULL"
        ).fetchone()[0],
        "embedded_chunks": db.execute(
            "SELECT COUNT(*) FROM chunks WHERE embedded_at IS NOT NULL"
        ).fetchone()[0],
        "scrape_log": {
            row[0]: row[1]
            for row in db.execute(
                "SELECT status, COUNT(*) FROM scrape_log GROUP BY status"
            ).fetchall()
        },
    }
