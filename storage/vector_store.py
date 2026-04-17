"""
Qdrant vector store — embedding + indexing pipeline.

Collection: pasco_chunks
  - Dense vector:  text-embedding-3-small (1536-dim, cosine)
  - Payload fields indexed: source, doc_type, date, url, document_id
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

import sqlite_utils
from fastembed import TextEmbedding
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    Range,
    VectorParams,
)

from config import settings

logger = logging.getLogger(__name__)

EMBED_MODEL = "BAAI/bge-small-en-v1.5"
VECTOR_SIZE = 384
BATCH_SIZE = 100

_embedder: TextEmbedding | None = None


def _get_embedder() -> TextEmbedding:
    global _embedder
    if _embedder is None:
        _embedder = TextEmbedding(model_name=EMBED_MODEL)
    return _embedder


def _embed_texts(texts: list[str]) -> list[list[float]]:
    embedder = _get_embedder()
    return [v.tolist() for v in embedder.embed(texts)]


def get_qdrant() -> QdrantClient:
    return QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key or None,
    )


def ensure_collection(client: QdrantClient) -> None:
    existing = [c.name for c in client.get_collections().collections]
    if settings.qdrant_collection not in existing:
        client.create_collection(
            collection_name=settings.qdrant_collection,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        # Index payload fields for filtered retrieval
        for field in ("source", "doc_type", "date", "document_id"):
            client.create_payload_index(
                collection_name=settings.qdrant_collection,
                field_name=field,
                field_schema="keyword",
            )
        logger.info("Created Qdrant collection: %s", settings.qdrant_collection)


def embed_and_index(db: sqlite_utils.Database, max_chunks: int = 0) -> int:
    """Embed unenriched chunks and upsert into Qdrant. Returns count indexed."""
    qdrant = get_qdrant()
    ensure_collection(qdrant)

    from storage.db import get_unembedded_chunks

    total = 0
    limit = max_chunks if max_chunks > 0 else 100_000

    while True:
        batch_limit = min(BATCH_SIZE, limit - total)
        if batch_limit <= 0:
            break
        chunks = get_unembedded_chunks(db, limit=batch_limit)
        if not chunks:
            break

        logger.info("Embedding batch of %d chunks", len(chunks))
        _embed_batch(qdrant, db, chunks)
        total += len(chunks)

        if len(chunks) < batch_limit:
            break

    logger.info("Indexed %d chunks into Qdrant", total)
    return total


def _embed_batch(
    qdrant: QdrantClient,
    db: sqlite_utils.Database,
    chunks: list[dict],
) -> None:
    texts = [c.get("summary") or c["text"] for c in chunks]
    vectors = _embed_texts(texts)

    points: list[PointStruct] = []
    for chunk, vector in zip(chunks, vectors):
        entity_tags = chunk.get("entity_tags") or "[]"
        if isinstance(entity_tags, str):
            try:
                entity_tags = json.loads(entity_tags)
            except Exception:
                entity_tags = []
        meta = chunk.get("metadata_json") or "{}"
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}

        points.append(
            PointStruct(
                id=_chunk_id_to_int(chunk["id"]),
                vector=vector,
                payload={
                    "chunk_id": chunk["id"],
                    "document_id": chunk["document_id"],
                    "source": chunk["source"],
                    "doc_type": chunk["doc_type"],
                    "chunk_index": chunk["chunk_index"],
                    "text": chunk["text"],
                    "summary": chunk.get("summary", ""),
                    "entity_tags": entity_tags,
                    "citizen_question": chunk.get("citizen_question", ""),
                    "date": chunk.get("date", ""),
                    "url": chunk.get("url", ""),
                    "metadata": meta,
                },
            )
        )

    qdrant.upsert(collection_name=settings.qdrant_collection, points=points)

    now = datetime.utcnow().isoformat()
    ids = [c["id"] for c in chunks]
    placeholders = ",".join("?" * len(ids))
    db.execute(
        f"UPDATE chunks SET embedded_at = ? WHERE id IN ({placeholders})",
        [now, *ids],
    )


def _chunk_id_to_int(chunk_id: str) -> int:
    """Convert a chunk string ID to a stable integer for Qdrant."""
    import hashlib
    return int(hashlib.md5(chunk_id.encode()).hexdigest()[:15], 16)


def dense_search(
    query: str,
    top_k: int = 20,
    source_filter: str | None = None,
    doc_type_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """Run dense vector search and return list of scored payload dicts."""
    qdrant = get_qdrant()
    query_vector = _embed_texts([query])[0]

    must_conditions = []
    if source_filter:
        must_conditions.append(FieldCondition(key="source", match=MatchValue(value=source_filter)))
    if doc_type_filter:
        must_conditions.append(FieldCondition(key="doc_type", match=MatchValue(value=doc_type_filter)))
    if date_from or date_to:
        range_kwargs: dict = {}
        if date_from:
            range_kwargs["gte"] = date_from
        if date_to:
            range_kwargs["lte"] = date_to
        must_conditions.append(FieldCondition(key="date", range=Range(**range_kwargs)))

    query_filter = Filter(must=must_conditions) if must_conditions else None

    results = qdrant.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    ).points
    return [
        {**r.payload, "_score": r.score, "_rank": i}
        for i, r in enumerate(results)
    ]
