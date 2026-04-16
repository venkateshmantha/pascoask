"""
Shared data models for the ingestion pipeline.
"""
from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class Document(BaseModel):
    """
    Normalized representation of any Pasco County public record.
    All scrapers must produce this shape before writing to SQLite.
    """

    id: str = Field(description="Stable, unique identifier. e.g. 'bcc_minutes_2024_03_12'")
    source: str = Field(description="bcc_minutes | ldc | civicclerk | gis")
    doc_type: str = Field(description="meeting_minutes | ordinance | zoning_code | staff_report")
    title: str
    body: str = Field(description="Full extracted text content")
    date: str = Field(default="", description="ISO 8601 date string, may be empty")
    url: str = Field(default="", description="Source URL")
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_db_row(self) -> dict[str, Any]:
        """Convert to a flat dict suitable for SQLite upsert."""
        import json
        return {
            "id": self.id,
            "source": self.source,
            "doc_type": self.doc_type,
            "title": self.title,
            "body": self.body,
            "date": self.date,
            "url": self.url,
            "metadata_json": json.dumps(self.metadata),
        }


class Chunk(BaseModel):
    """A chunked piece of a Document, ready for enrichment and embedding."""

    id: str
    document_id: str
    source: str
    doc_type: str
    chunk_index: int
    text: str
    date: str = ""
    url: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    # Filled in by enricher
    summary: str = ""
    entity_tags: list[str] = Field(default_factory=list)
    citizen_question: str = ""
    enriched_at: str | None = None
    embedded_at: str | None = None

    def to_db_row(self) -> dict[str, Any]:
        import json
        return {
            "id": self.id,
            "document_id": self.document_id,
            "source": self.source,
            "doc_type": self.doc_type,
            "chunk_index": self.chunk_index,
            "text": self.text,
            "summary": self.summary,
            "entity_tags": json.dumps(self.entity_tags),
            "citizen_question": self.citizen_question,
            "date": self.date,
            "url": self.url,
            "metadata_json": json.dumps(self.metadata),
            "enriched_at": self.enriched_at,
            "embedded_at": self.embedded_at,
        }
