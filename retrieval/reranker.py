"""
Cross-encoder re-ranker using cross-encoder/ms-marco-MiniLM-L-6-v2.
Scores top-N candidate chunks against the query and returns top-K.
"""
from __future__ import annotations

import logging
from functools import lru_cache

logger = logging.getLogger(__name__)

MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"


@lru_cache(maxsize=1)
def _get_cross_encoder():
    from sentence_transformers import CrossEncoder
    logger.info("Loading cross-encoder model: %s", MODEL_NAME)
    return CrossEncoder(MODEL_NAME)


def rerank(query: str, candidates: list[dict], top_k: int = 5) -> list[dict]:
    """
    Re-score candidates with a cross-encoder and return top_k.
    Each candidate must have a 'text' field.
    Falls back to original order if model fails.
    """
    if not candidates:
        return []
    if len(candidates) <= top_k:
        return candidates

    try:
        ce = _get_cross_encoder()
        pairs = [(query, c.get("text", "")[:512]) for c in candidates]
        scores = ce.predict(pairs)
        ranked = sorted(zip(scores, candidates), key=lambda x: x[0], reverse=True)
        return [
            {**chunk, "_rerank_score": float(score), "_rank": i}
            for i, (score, chunk) in enumerate(ranked[:top_k])
        ]
    except Exception as exc:
        logger.warning("Reranking failed, using original order: %s", exc)
        return candidates[:top_k]
