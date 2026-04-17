# PascoAsk — Claude Code Handoff Document

You are continuing the implementation of **PascoAsk** — a natural language agent over Pasco County, FL public government records. Milestone 1 is already complete and committed in the repo. Your job is to implement Milestones 2 through 8 sequentially, running and verifying each milestone before moving to the next.

---

## Project Overview

PascoAsk lets citizens ask natural language questions like:
- *"What did the commissioners decide about the SR-54 corridor development?"*
- *"What are the setback requirements for a residential fence?"*
- *"Has there been any discussion about flood zone changes near Zephyrhills?"*

...and get grounded, cited answers linked directly to actual Pasco County government documents.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Data Sources                       │
│  BCC Minutes │ Land Dev Code │ GIS Data │ CivicClerk │
└──────────────────────┬──────────────────────────────┘
                       │
              Stage 1: Ingestion (scrapers → SQLite)
                       │
              Stage 2: Chunking + LLM Enrichment (claude-opus-4-6)
                       │
              Stage 3: Qdrant (hybrid dense + BM25 index)
                       │
         ┌─────────────┼──────────────┐
         │             │              │
    MCP Server    FastAPI BFF     Eval Harness (RAGAS)
         │             │
    Claude Desktop  Next.js Chat UI
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM (enrichment + answers) | `claude-opus-4-6` (Anthropic API) |
| Embeddings | `text-embedding-3-small` (OpenAI) |
| Vector DB | Qdrant (Docker local / Qdrant Cloud free tier) |
| Re-ranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` (HuggingFace, runs locally) |
| PDF extraction | `pdfplumber` |
| MCP server | Python MCP SDK (`mcp` package) |
| API | FastAPI with SSE streaming |
| Frontend | Next.js 14 + Tailwind CSS + Vercel AI SDK |
| Eval | RAGAS |
| Raw storage | SQLite via `sqlite-utils` |

---

## Repository Structure (Milestone 1 complete)

```
pascoask/
├── pyproject.toml
├── .env.example                  ← copy to .env and fill in API keys
├── .gitignore
├── README.md
├── config.py                     ← all settings via pydantic-settings + .env
│
├── ingestion/
│   ├── models.py                 ← Document + Chunk pydantic models
│   ├── pipeline.py               ← CLI orchestrator (python -m ingestion.pipeline)
│   └── scrapers/
│       ├── base.py               ← BaseScraper: rate limiting, retry, resumability
│       └── bcc_minutes.py        ← ✅ DONE: BCC meeting minutes scraper
│
├── storage/
│   └── db.py                     ← SQLite schema + helpers (sqlite-utils)
│
├── retrieval/                    ← empty, to be built in M6
├── mcp_server/                   ← empty, to be built in M7
├── api/                          ← empty, to be built in M8
├── eval/
│   └── results/
├── scripts/
│   └── db_stats.py               ← rich CLI: show DB status
└── ui/                           ← empty, to be built in M8
```

---

## Data Sources

All freely available under Florida's Public Records Law (Chapter 119, Florida Statutes). No auth required.

| Source | Content | URL | `doc_type` |
|---|---|---|---|
| BCC Meeting Minutes | Commission decisions, votes, public hearings | `pascocountyfl.gov/government/agendas_minutes.php` | `meeting_minutes` |
| Land Development Code | Zoning rules, setbacks, permitted uses, code enforcement | `pascocountyfl.gov/services/planning_and_development/land_development_code_amendments.php` | `ordinance` |
| CivicClerk Portal | Individual agenda items + staff report PDFs | `pascocofl.portal.civicclerk.com` | `staff_report` |
| GIS Zoning Data | Zoning boundaries, FEMA zones, land use (shapefiles) | `pascocountyfl.gov/services/gis/data.php` | `zoning_code` |

---

## Document Schema (defined in `ingestion/models.py`)

```python
Document:
  id: str           # e.g. "bcc_minutes_2024_03_12", "ldc_section_602_11"
  source: str       # bcc_minutes | ldc | civicclerk | gis
  doc_type: str     # meeting_minutes | ordinance | zoning_code | staff_report
  title: str
  body: str         # full extracted text
  date: str         # ISO 8601 date string
  url: str          # source URL
  metadata: dict    # source-specific extras

Chunk:
  id: str           # "{doc_id}_chunk_{n}"
  document_id: str
  source: str
  doc_type: str
  chunk_index: int
  text: str         # raw chunk text
  summary: str      # filled by LLM enrichment
  entity_tags: list # filled by LLM enrichment
  citizen_question: str  # filled by LLM enrichment
  date: str
  url: str
  metadata: dict
  enriched_at: str | None
  embedded_at: str | None
```

---

## SQLite Schema (defined in `storage/db.py`)

Three tables:
- `documents` — raw normalized documents from all scrapers
- `chunks` — chunked + enriched pieces ready for embedding
- `scrape_log` — resumability: tracks URL status (success/error/skipped) so re-runs skip already-done work

---

## Chunking Strategy

**Do NOT use naive character-count splitting.** Each source has a deliberate strategy:

| Source | Strategy |
|---|---|
| **BCC Minutes** | Chunk by agenda item. Each item number + discussion + vote = one chunk. Never split mid-item. |
| **LDC** | Chunk by subsection (e.g. "Section 602.11"). Keep the section number in the chunk text itself for exact-match retrieval. |
| **CivicClerk staff reports** | Chunk by PDF heading boundaries (##). |
| **GIS zoning records** | Each zoning code is already one small record — no splitting needed. |

All chunks carry forward the parent document's `date`, `url`, `source`, `doc_type`, and `metadata`.

---

## LLM Enrichment (`claude-opus-4-6`)

For every chunk, call `claude-opus-4-6` with this exact system prompt:

```
You are a data enrichment assistant for a Pasco County, FL government records search system.
Given a chunk of text from a government document, extract:
1. summary: A 1-2 sentence plain-English summary a non-lawyer citizen can understand
2. entity_tags: List of specific entities mentioned (street names, zoning codes, ordinance numbers, commissioner names, dollar amounts, dates, project names)
3. citizen_question: The single most likely question a Pasco County resident would ask that this chunk answers

Respond only in JSON with no preamble or markdown: {"summary": "...", "entity_tags": [...], "citizen_question": "..."}
```

Implementation notes:
- Process in batches of 20 chunks
- Use `tenacity` for retry (exponential backoff, 4 attempts)
- After enrichment, write `summary`, `entity_tags`, `citizen_question`, and `enriched_at` back to the `chunks` table
- **Important**: `claude-opus-4-6` does NOT support prefilled assistant messages — do not use them

---

## Qdrant Setup

```python
# Collection config
collection_name = "pasco_chunks"
vector_size = 1536          # text-embedding-3-small dimensions
distance = "Cosine"
# Enable sparse vectors for BM25 hybrid search

# Payload fields to index (for filtered retrieval):
# source, doc_type, date, url, document_id
```

Run Qdrant locally via Docker:
```bash
docker run -p 6333:6333 -v $(pwd)/qdrant_storage:/qdrant/storage qdrant/qdrant
```

---

## RAG Query Pipeline

Full query flow:

```
User question
    ↓
1. Query expansion (claude-opus-4-6 generates 2-3 alternative phrasings)
    ↓
2. Parallel hybrid search:
   - Dense: text-embedding-3-small vector search in Qdrant
   - Sparse: BM25 keyword search in Qdrant
    ↓
3. Reciprocal Rank Fusion (RRF) merges ranked lists
    ↓
4. Optional payload filtering (by source, doc_type, date_from, date_to)
    ↓
5. Cross-encoder re-ranking: top 10 → top 5
   Model: cross-encoder/ms-marco-MiniLM-L-6-v2 (HuggingFace, runs locally)
    ↓
6. Answer generation (claude-opus-4-6) with retrieved chunks as context
   Produce cited answer with source URLs
```

---

## MCP Server — 5 Tools

Implement in `mcp_server/tools.py` and `mcp_server/server.py`:

```python
search_pasco_records(
    query: str,
    doc_type: Optional[Literal["meeting_minutes", "ordinance", "zoning_code", "staff_report"]] = None,
    date_from: Optional[str] = None,   # ISO 8601
    date_to: Optional[str] = None
) -> List[RetrievedChunk]
# Core hybrid search with optional filters

get_zoning_rules(zoning_code: str) -> ZoningCodeDetail
# Direct lookup: "what does MPUD allow?"

get_meeting_item(meeting_date: str, item_number: int) -> MeetingItem
# Precise retrieval of a specific agenda item + vote result

find_commissioner_votes(
    topic: str,
    commissioner: Optional[str] = None
) -> List[VoteRecord]
# "How did District 2 vote on development issues in 2024?"

summarize_topic(
    topic: str,
    date_range: Optional[str] = None
) -> TopicSummary
# "What has the county decided about affordable housing in the last 2 years?"
```

**Transports:**
- `stdio` — for Claude Desktop (local use)
- `HTTP/SSE` — for the Next.js UI backend

**Claude Desktop config** (add after M7 is working):
```json
{
  "mcpServers": {
    "pascoask": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/pascoask"
    }
  }
}
```

---

## FastAPI Backend (`api/main.py`)

Single streaming endpoint:

```
POST /chat
Body:    { "message": str, "history": [...] }
Response: Server-Sent Events stream

SSE event types:
  {"type": "token",   "content": "..."}   ← streaming answer token
  {"type": "sources", "chunks": [...]}    ← retrieved chunks with URLs (sent first)
```

---

## Next.js UI (`ui/`)

**Stack:** Next.js 14 + Tailwind CSS + Vercel AI SDK (`useChat` hook)

**Three-panel layout:**
1. **Chat panel** — streaming responses with inline citations linked to source government document URLs
2. **Sources sidebar** — retrieved chunks with relevance scores, source type badge (Minutes / LDC / Zoning / Staff Report), direct link to PDF
3. **Search mode toggle** — "Semantic only" vs "Hybrid" — visually demonstrates the retrieval difference

**Extra feature:** "Plain English" button on any LDC section chunk — calls the API to translate legal language into a 3-sentence plain-English summary. Makes the demo viscerally compelling.

**Deploy targets:**
- Frontend → Vercel (free tier)
- Vector DB → Qdrant Cloud (1GB free tier)
- FastAPI + MCP backend → Railway (~$5/month)

---

## Eval Harness (`eval/`)

Create `eval/golden_qa.json` with 40 question-answer pairs derived from **actually reading retrieved documents** (do not invent answers). Structure:

```json
[
  {
    "question": "What vote did the commissioners take on the Mirada development?",
    "expected_answer": "...",
    "source_doc_id": "bcc_minutes_2023_...",
    "doc_type": "meeting_minutes"
  }
]
```

Leave the file as a TODO with 3 placeholder entries and a comment instructing the developer to fill it in after running the scrapers and reading actual documents.

Run with:
```bash
python eval/run_eval.py
```

RAGAS metrics to measure: `faithfulness`, `answer_relevancy`, `context_precision`.
Save results to `eval/results/{timestamp}.json`.

---

## Constraints & Conventions

- **Model string**: always use `claude-opus-4-6` — not `opus-4`, not `claude-3-opus`
- **No prefills**: `claude-opus-4-6` does NOT support prefilled assistant messages — requests will return 400
- **Rate limiting**: all HTTP scraping must respect 1.5s between requests (already enforced in `BaseScraper`)
- **Resumability**: all scrapers must check `scrape_log` before fetching — never re-scrape a successful URL
- **PDF cache**: store downloaded PDFs in `data/pdfs/` — never re-download a cached PDF
- **Retry**: use `tenacity` for all API calls (exponential backoff, 4 attempts max)
- **Pydantic v2** throughout
- **Rich** for all CLI output
- Run `ruff` for linting before each commit
- **Do not invent data** — the golden QA eval pairs must come from actually reading real county documents

---

## Running the Project

```bash
# Setup
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env            # fill in ANTHROPIC_API_KEY and OPENAI_API_KEY

# Start Qdrant (Docker required)
docker run -p 6333:6333 qdrant/qdrant

# Run scrapers
python -m ingestion.pipeline --scrapers bcc_minutes
python -m ingestion.pipeline --scrapers all

# Check DB status
python scripts/db_stats.py

# Query CLI (after M5)
python scripts/query.py "your question here"

# Run eval (after M6)
python eval/run_eval.py

# Start API server (after M8)
uvicorn api.main:app --reload
```

---

## Milestones

### ✅ Milestone 1 — Project scaffold + BCC minutes scraper
Complete. Includes: full project structure, config, SQLite schema, Document/Chunk models, BaseScraper (rate limiting + retry + resumability), BCCMinutesScraper (PDF discovery + download + pdfplumber extraction + date parsing + PDF caching), pipeline CLI, db_stats script.

---

### Milestone 2 — LDC + CivicClerk scrapers

**`ingestion/scrapers/ldc.py`**
- Scrape Land Development Code PDFs from the planning amendments page
- Each LDC section becomes a Document with `doc_type="ordinance"`
- Doc ID format: `"ldc_section_{number}"` e.g. `"ldc_section_602_11"`
- Parse section numbers from PDF text/filenames for metadata

**`ingestion/scrapers/civicclerk.py`**
- Scrape `pascocofl.portal.civicclerk.com` for individual agenda items
- Each agenda item + its attached staff report PDF = one Document with `doc_type="staff_report"`
- Carry forward: meeting date, item number, department, item title

**Wire both into `ingestion/pipeline.py`**

**Verify**: run `python -m ingestion.pipeline --scrapers ldc civicclerk`, then `python scripts/db_stats.py` and confirm document rows exist with correct `source` and `doc_type` values.

---

### Milestone 3 — Chunking pipeline

**`ingestion/chunker.py`**

Implement `chunk_document(doc: Document) -> List[Chunk]` with per-source routing:
- `bcc_minutes` → split by agenda item number regex
- `ldc` → split by section/subsection heading, keep section number in chunk text
- `civicclerk` → split by PDF heading boundaries
- `gis` → return single chunk per document

Add chunking step to `ingestion/pipeline.py` so it runs automatically after scraping.

**Verify**: check chunk counts per source in `db_stats.py`. Manually inspect 5 chunks from each source to confirm they make sense.

---

### Milestone 4 — LLM enrichment

**`ingestion/enricher.py`**

- Load unenriched chunks from SQLite (use `storage/db.get_unenriched_chunks()`)
- Call `claude-opus-4-6` in batches of 20 with the enrichment prompt above
- Parse JSON response, write `summary`, `entity_tags`, `citizen_question`, `enriched_at` back to DB
- Add `--enrich` flag to `ingestion/pipeline.py`

**Verify**: run enrichment on 50 chunks, inspect outputs in DB. Summaries should be plain English. Entity tags should include zoning codes, street names, dates. Citizen questions should sound like real resident questions.

---

### Milestone 5 — Qdrant embedding + indexing

**`storage/vector_store.py`**

- Initialize Qdrant collection with dense (1536-dim cosine) + sparse (BM25) vectors
- Embed chunks using `text-embedding-3-small` — embed the `summary` field (not raw text)
- Store raw `text` as payload alongside embedding
- Batch embed in groups of 100, mark `embedded_at` in SQLite after success

**`scripts/query.py`**
- Simple CLI: `python scripts/query.py "can I keep chickens in a residential zone?"`
- Run dense-only search, print top 5 results with scores and source URLs

**Verify**: run 5 diverse test queries covering all 4 source types. Results should be semantically relevant.

---

### Milestone 6 — Full RAG query pipeline

**`retrieval/query_expansion.py`** — call `claude-opus-4-6` to generate 2-3 rephrasings of a query

**`retrieval/hybrid_search.py`** — run dense + sparse search in parallel, merge with RRF

**`retrieval/reranker.py`** — load `cross-encoder/ms-marco-MiniLM-L-6-v2`, re-score top 10 → top 5

**`retrieval/pipeline.py`** — orchestrate: expand → hybrid search → filter → rerank → generate answer

Update `scripts/query.py` to use the full pipeline.

**Verify**: run the same 5 queries from M5 through the full pipeline. Compare hybrid vs semantic-only results. Answers should be grounded with source citations.

---

### Milestone 7 — MCP server

**`mcp_server/tools.py`** — implement all 5 tools wrapping `retrieval/pipeline.py`

**`mcp_server/server.py`** — register tools, expose via:
- `stdio` transport (for Claude Desktop)
- `HTTP/SSE` transport on port 8001 (for web UI)

**Verify**: 
1. Connect to Claude Desktop using the config above
2. Ask 3 test questions directly in Claude Desktop
3. Confirm cited answers with source URLs are returned

---

### Milestone 8 — FastAPI backend + Next.js UI

**`api/main.py`**
- `POST /chat` — streaming SSE endpoint
- `GET /health` — health check

**`ui/`** — scaffold with `npx create-next-app@latest ui --typescript --tailwind`
- Implement chat panel with `useChat` from Vercel AI SDK
- Sources sidebar showing retrieved chunks
- Search mode toggle (semantic vs hybrid)
- "Plain English" button on LDC chunks

**Deploy**:
- `ui/` → Vercel
- Qdrant → Qdrant Cloud free tier
- `api/` + `mcp_server/` → Railway

Update `README.md` with live demo URL and eval results.

---

## Definition of Done

The project is complete when:
- [ ] All 4 data sources are scraped, chunked, enriched, and indexed
- [ ] `python scripts/query.py "what are the rules for building a fence in a residential zone?"` returns a cited answer with a link to the actual LDC section
- [ ] Claude Desktop can answer Pasco County questions via the MCP server
- [ ] The Next.js UI is deployed and publicly accessible
- [ ] `eval/run_eval.py` produces a RAGAS report with faithfulness score documented in README
- [ ] A 2-minute demo GIF is recorded and added to README
