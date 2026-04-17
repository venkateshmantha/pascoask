"""
Hybrid search — parallel dense (Qdrant) + BM25 keyword search, merged with RRF.
"""
from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import settings
from storage.vector_store import dense_search

logger = logging.getLogger(__name__)

# BM25 parameters
BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60  # constant for Reciprocal Rank Fusion


def hybrid_search(
    queries: list[str],
    top_k: int = 20,
    source_filter: str | None = None,
    doc_type_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    corpus: list[dict] | None = None,
) -> list[dict]:
    """
    Run dense + BM25 search for each query in parallel, merge with RRF.

    Args:
        queries: Original query + expanded alternatives.
        corpus: Pre-loaded chunk dicts for BM25. If None, BM25 is skipped.
        top_k: Number of results to return after fusion.
    """
    dense_results: list[list[dict]] = []
    bm25_results: list[list[dict]] = []

    with ThreadPoolExecutor(max_workers=min(len(queries) * 2, 8)) as pool:
        dense_futures = {
            pool.submit(
                dense_search,
                q,
                top_k * 2,
                source_filter,
                doc_type_filter,
                date_from,
                date_to,
            ): ("dense", i)
            for i, q in enumerate(queries)
        }
        bm25_futures = {}
        if corpus:
            bm25_futures = {
                pool.submit(bm25_search, q, corpus, top_k * 2): ("bm25", i)
                for i, q in enumerate(queries)
            }

        all_futures = {**dense_futures, **bm25_futures}
        ranked_lists: dict[str, list[list[dict]]] = {"dense": [], "bm25": []}

        for fut in as_completed(all_futures):
            kind, _ = all_futures[fut]
            try:
                results = fut.result()
                ranked_lists[kind].append(results)
            except Exception as exc:
                logger.warning("Search error (%s): %s", kind, exc)

    merged_dense = _rrf_merge(ranked_lists["dense"], top_k * 2)
    merged_bm25 = _rrf_merge(ranked_lists["bm25"], top_k * 2) if ranked_lists["bm25"] else []

    final = _rrf_merge([merged_dense, merged_bm25], top_k) if merged_bm25 else merged_dense[:top_k]
    return final


def bm25_search(query: str, corpus: list[dict], top_k: int = 20) -> list[dict]:
    """Simple BM25 search over an in-memory corpus of chunk dicts."""
    tokens = _tokenize(query)
    if not tokens:
        return []

    # Build doc lengths + avgdl on the fly
    texts = [c.get("text", "") for c in corpus]
    doc_tokens = [_tokenize(t) for t in texts]
    avgdl = sum(len(dt) for dt in doc_tokens) / max(len(doc_tokens), 1)

    # IDF per query token
    N = len(corpus)
    idf: dict[str, float] = {}
    for tok in set(tokens):
        df = sum(1 for dt in doc_tokens if tok in dt)
        idf[tok] = math.log((N - df + 0.5) / (df + 0.5) + 1)

    scores: list[tuple[float, int]] = []
    for i, (doc, dt) in enumerate(zip(corpus, doc_tokens)):
        if not dt:
            continue
        dl = len(dt)
        score = 0.0
        freq_map: dict[str, int] = {}
        for t in dt:
            freq_map[t] = freq_map.get(t, 0) + 1
        for tok in tokens:
            tf = freq_map.get(tok, 0)
            if tf == 0:
                continue
            tf_norm = (tf * (BM25_K1 + 1)) / (tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl))
            score += idf.get(tok, 0) * tf_norm
        if score > 0:
            scores.append((score, i))

    scores.sort(reverse=True)
    return [
        {**corpus[i], "_score": s, "_rank": rank}
        for rank, (s, i) in enumerate(scores[:top_k])
    ]


def _rrf_merge(ranked_lists: list[list[dict]], top_k: int) -> list[dict]:
    """Reciprocal Rank Fusion over multiple ranked result lists."""
    if not ranked_lists:
        return []
    if len(ranked_lists) == 1:
        return ranked_lists[0][:top_k]

    rrf_scores: dict[str, float] = defaultdict(float)
    chunk_by_id: dict[str, dict] = {}

    for ranked in ranked_lists:
        for rank, item in enumerate(ranked):
            cid = item.get("chunk_id") or item.get("id", str(rank))
            rrf_scores[cid] += 1.0 / (RRF_K + rank + 1)
            if cid not in chunk_by_id:
                chunk_by_id[cid] = item

    sorted_ids = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)
    return [
        {**chunk_by_id[cid], "_rrf_score": rrf_scores[cid], "_rank": i}
        for i, cid in enumerate(sorted_ids[:top_k])
    ]


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\b[a-z0-9]+\b", text.lower())
