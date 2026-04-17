"""
Query expansion — uses claude-opus-4-6 to generate 2-3 alternative phrasings.
"""
from __future__ import annotations

import json
import logging

import anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from config import settings

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a search query expansion assistant for a Pasco County, FL government records system. "
    "Given a user question, generate 2-3 alternative phrasings that would help retrieve relevant "
    "government documents. Include variations that use official government terminology. "
    "Respond ONLY with a JSON array of strings: [\"phrase1\", \"phrase2\", \"phrase3\"]"
)


@retry(
    retry=retry_if_exception_type(Exception),
    wait=wait_exponential(multiplier=2, min=2, max=30),
    stop=stop_after_attempt(4),
    reraise=True,
)
def expand_query(query: str) -> list[str]:
    """Return [original] + 2-3 LLM-generated alternative phrasings."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    message = client.messages.create(
        model=settings.answer_model,
        max_tokens=256,
        system=_SYSTEM,
        messages=[{"role": "user", "content": query}],
    )
    raw = message.content[0].text.strip()
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        alternatives = json.loads(raw)
        if isinstance(alternatives, list):
            return [query] + [str(a) for a in alternatives if str(a) != query]
    except Exception:
        logger.warning("Query expansion parse failed, using original")
    return [query]
