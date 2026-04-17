"""
Full RAG query pipeline:
  1. Query expansion (claude-opus-4-6)
  2. Parallel hybrid search (dense + BM25) with RRF
  3. Optional payload filtering
  4. Cross-encoder re-ranking (top 10 → top 5)
  5. Answer generation (claude-opus-4-6) with source citations
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import settings
from retrieval.hybrid_search import hybrid_search
from retrieval.query_expansion import expand_query
from retrieval.reranker import rerank

logger = logging.getLogger(__name__)

_ANSWER_SYSTEM = (
    "You are PascoAsk, a helpful assistant that answers questions about Pasco County, FL "
    "government records. Use ONLY the provided source documents to answer. "
    "For each claim, cite the source by including [Source N] inline. "
    "If the documents don't contain enough information, say so clearly. "
    "Be concise and use plain English that any citizen can understand."
)


@dataclass
class RetrievedChunk:
    chunk_id: str
    document_id: str
    source: str
    doc_type: str
    text: str
    summary: str
    url: str
    date: str
    score: float
    entity_tags: list[str] = field(default_factory=list)
    citizen_question: str = ""


@dataclass
class QueryResult:
    question: str
    answer: str
    sources: list[RetrievedChunk]
    expanded_queries: list[str]


def query(
    question: str,
    doc_type: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_k: int = 5,
    expand: bool = True,
    use_hybrid: bool = True,
) -> QueryResult:
    """Run the full RAG pipeline for a user question."""

    # 1. Query expansion
    queries = expand_query(question) if expand else [question]
    logger.info("Expanded to %d queries: %s", len(queries), queries)

    # 2. BM25 corpus (load from SQLite for keyword search)
    corpus = _load_corpus(source, doc_type) if use_hybrid else None

    # 3. Hybrid search
    candidates = hybrid_search(
        queries=queries,
        top_k=20,
        source_filter=source,
        doc_type_filter=doc_type,
        date_from=date_from,
        date_to=date_to,
        corpus=corpus,
    )
    logger.info("Retrieved %d candidates", len(candidates))

    # 4. Re-rank top 10 → top 5
    top_candidates = rerank(question, candidates[:10], top_k=top_k)

    # 5. Build source objects
    sources = [_to_retrieved_chunk(c) for c in top_candidates]

    # 6. Generate answer
    answer = _generate_answer(question, sources)

    return QueryResult(
        question=question,
        answer=answer,
        sources=sources,
        expanded_queries=queries,
    )


def query_stream(
    question: str,
    doc_type: str | None = None,
    source: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_k: int = 5,
):
    """Generator that yields (event_type, data) tuples for SSE streaming."""
    queries = expand_query(question)
    corpus = _load_corpus(source, doc_type)

    candidates = hybrid_search(
        queries=queries, top_k=20,
        source_filter=source, doc_type_filter=doc_type,
        date_from=date_from, date_to=date_to, corpus=corpus,
    )
    top_candidates = rerank(question, candidates[:10], top_k=top_k)
    sources = [_to_retrieved_chunk(c) for c in top_candidates]

    # Emit sources first so UI can render them before the answer streams
    import dataclasses
    yield "sources", [dataclasses.asdict(s) for s in sources]

    # Stream the answer
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    context = _build_context(sources)
    with client.messages.stream(
        model=settings.answer_model,
        max_tokens=1024,
        system=_ANSWER_SYSTEM,
        messages=[{"role": "user", "content": f"Question: {question}\n\n{context}"}],
    ) as stream:
        for text in stream.text_stream:
            yield "token", text


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_corpus(
    source_filter: str | None = None,
    doc_type_filter: str | None = None,
) -> list[dict]:
    """Load chunk text from SQLite for BM25. Apply filters if given."""
    from storage.db import get_db
    db = get_db()
    where = "WHERE enriched_at IS NOT NULL"
    params: list = []
    if source_filter:
        where += " AND source = ?"
        params.append(source_filter)
    if doc_type_filter:
        where += " AND doc_type = ?"
        params.append(doc_type_filter)
    rows = db.execute(
        f"SELECT id, document_id, source, doc_type, chunk_index, text, "
        f"summary, entity_tags, citizen_question, date, url "
        f"FROM chunks {where} LIMIT 10000",
        params,
    ).fetchall()
    cols = ["chunk_id", "document_id", "source", "doc_type", "chunk_index", "text",
            "summary", "entity_tags", "citizen_question", "date", "url"]
    return [dict(zip(cols, r)) for r in rows]


def _to_retrieved_chunk(c: dict) -> RetrievedChunk:
    import json
    tags = c.get("entity_tags") or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = []
    return RetrievedChunk(
        chunk_id=c.get("chunk_id", ""),
        document_id=c.get("document_id", ""),
        source=c.get("source", ""),
        doc_type=c.get("doc_type", ""),
        text=c.get("text", ""),
        summary=c.get("summary", ""),
        url=c.get("url", ""),
        date=c.get("date", ""),
        score=float(c.get("_rrf_score") or c.get("_score") or 0),
        entity_tags=tags,
        citizen_question=c.get("citizen_question", ""),
    )


def _build_context(sources: list[RetrievedChunk]) -> str:
    parts = ["Source documents:\n"]
    for i, s in enumerate(sources, 1):
        parts.append(
            f"[Source {i}] ({s.source} | {s.doc_type} | {s.date})\n"
            f"URL: {s.url}\n"
            f"{s.text[:1200]}\n"
        )
    return "\n".join(parts)


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def _generate_answer(question: str, sources: list[RetrievedChunk]) -> str:
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    context = _build_context(sources)
    message = client.messages.create(
        model=settings.answer_model,
        max_tokens=1024,
        system=_ANSWER_SYSTEM,
        messages=[{"role": "user", "content": f"Question: {question}\n\n{context}"}],
    )
    return message.content[0].text
