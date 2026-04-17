"""
PascoAsk FastAPI backend.

Endpoints:
  POST /chat   — streaming SSE: returns sources then token-by-token answer
  GET  /health — health check
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="PascoAsk API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = []
    doc_type: str | None = None
    source: str | None = None
    date_from: str | None = None
    date_to: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pascoask"}


@app.post("/chat")
async def chat(req: ChatRequest):
    """
    Streaming chat endpoint.

    SSE event format:
      data: {"type": "sources", "chunks": [...]}
      data: {"type": "token",   "content": "..."}
      data: {"type": "done"}
    """
    from retrieval.pipeline import query_stream

    def _generate():
        try:
            for event_type, data in query_stream(
                question=req.message,
                doc_type=req.doc_type,
                source=req.source,
                date_from=req.date_from,
                date_to=req.date_to,
            ):
                if event_type == "sources":
                    payload = json.dumps({"type": "sources", "chunks": data})
                else:
                    payload = json.dumps({"type": "token", "content": data})
                yield f"data: {payload}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"
        except Exception as exc:
            logger.exception("Chat stream error")
            err = json.dumps({"type": "error", "message": str(exc)})
            yield f"data: {err}\n\n"

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
