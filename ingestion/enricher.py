"""
LLM enrichment pipeline — calls claude-opus-4-6 to add summary, entity_tags,
and citizen_question to each unenriched chunk.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

import sqlite_utils

from config import settings
from storage.db import get_unenriched_chunks, upsert_chunk

logger = logging.getLogger(__name__)

ENRICHMENT_SYSTEM = (
    "You are a data enrichment assistant for a Pasco County, FL government records search system.\n"
    "Given a chunk of text from a government document, extract:\n"
    "1. summary: A 1-2 sentence plain-English summary a non-lawyer citizen can understand\n"
    "2. entity_tags: List of specific entities mentioned (street names, zoning codes, "
    "ordinance numbers, commissioner names, dollar amounts, dates, project names)\n"
    "3. citizen_question: The single most likely question a Pasco County resident would ask "
    "that this chunk answers\n\n"
    'Respond only in JSON with no preamble or markdown: {"summary": "...", "entity_tags": [...], "citizen_question": "..."}'
)

BATCH_SIZE = 20


def enrich_chunks(db: sqlite_utils.Database, max_chunks: int = 0) -> int:
    """
    Enrich all unenriched chunks. Returns the number of chunks enriched.
    max_chunks=0 means no limit.
    """
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    total = 0
    limit = max_chunks if max_chunks > 0 else 10_000

    while True:
        batch_size = min(BATCH_SIZE, limit - total)
        if batch_size <= 0:
            break

        chunks = get_unenriched_chunks(db, limit=batch_size)
        if not chunks:
            break

        logger.info("Enriching batch of %d chunks", len(chunks))
        _enrich_batch(client, db, chunks)
        total += len(chunks)

        if len(chunks) < batch_size:
            break  # exhausted

    logger.info("Enrichment complete: %d chunks enriched", total)
    return total


def _enrich_batch(
    client: anthropic.Anthropic,
    db: sqlite_utils.Database,
    chunks: list[dict],
) -> None:
    for chunk in chunks:
        try:
            result = _call_llm(client, chunk["text"])
            db.execute(
                """UPDATE chunks
                   SET summary = ?, entity_tags = ?, citizen_question = ?, enriched_at = ?
                   WHERE id = ?""",
                [
                    result.get("summary", ""),
                    json.dumps(result.get("entity_tags", [])),
                    result.get("citizen_question", ""),
                    datetime.utcnow().isoformat(),
                    chunk["id"],
                ],
            )
            db.conn.commit()
        except Exception as exc:
            logger.error("Enrichment failed for chunk %s: %s", chunk["id"], exc)


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _call_llm(client: anthropic.Anthropic, text: str) -> dict:
    message = client.messages.create(
        model=settings.enrichment_model,
        max_tokens=512,
        system=ENRICHMENT_SYSTEM,
        messages=[{"role": "user", "content": text[:3000]}],
    )
    raw = message.content[0].text.strip()
    # Strip any accidental markdown fences
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)
