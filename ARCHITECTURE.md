# PascoAsk — Architecture & Component Guide

A deep dive into how every piece of the system works, how they call each other, and how it all fits together. Written for learning purposes.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Repository Layout](#2-repository-layout)
3. [Configuration (`config.py`)](#3-configuration)
4. [Data Models (`ingestion/models.py`)](#4-data-models)
5. [Storage Layer (`storage/db.py`)](#5-storage-layer)
6. [Ingestion Pipeline](#6-ingestion-pipeline)
   - 6a. [Base Scraper](#6a-base-scraper)
   - 6b. [BCC Minutes Scraper](#6b-bcc-minutes-scraper)
   - 6c. [CivicClerk Scraper](#6c-civicclerk-scraper)
   - 6d. [LDC Scraper](#6d-ldc-scraper)
   - 6e. [Chunker](#6e-chunker)
   - 6f. [Enricher (LLM tagging)](#6f-enricher)
   - 6g. [Pipeline Orchestrator](#6g-pipeline-orchestrator)
7. [Vector Store (`storage/vector_store.py`)](#7-vector-store)
8. [Retrieval Pipeline](#8-retrieval-pipeline)
   - 8a. [Query Expansion](#8a-query-expansion)
   - 8b. [Hybrid Search (BM25 + Dense)](#8b-hybrid-search)
   - 8c. [Re-ranker](#8c-re-ranker)
   - 8d. [Answer Generation & Streaming](#8d-answer-generation--streaming)
9. [FastAPI Backend (`api/main.py`)](#9-fastapi-backend)
10. [Next.js Frontend (`ui/`)](#10-nextjs-frontend)
11. [MCP Server (`mcp_server/`)](#11-mcp-server)
12. [End-to-End Request Flow](#12-end-to-end-request-flow)
13. [How to Run Everything](#13-how-to-run-everything)
14. [Key Design Decisions & Trade-offs](#14-key-design-decisions--trade-offs)

---

## 1. System Overview

PascoAsk is a **Retrieval-Augmented Generation (RAG)** application that lets citizens ask plain-English questions about Pasco County, FL government records and get cited, accurate answers.

```
┌─────────────────────────────────────────────────────┐
│                  DATA SOURCES                       │
│  CivicClerk OData API   LDC Amendments PDF site     │
└────────────────┬────────────────────────────────────┘
                 │ scrape
                 ▼
┌─────────────────────────────────────────────────────┐
│               INGESTION PIPELINE                    │
│  Scrapers → Chunker → Enricher (Haiku LLM)          │
│              ↓                                      │
│         SQLite DB  (documents + chunks)             │
└────────────────┬────────────────────────────────────┘
                 │ embed
                 ▼
┌─────────────────────────────────────────────────────┐
│              VECTOR STORE                           │
│  fastembed (local ONNX) → Qdrant Cloud              │
└────────────────┬────────────────────────────────────┘
                 │ query
                 ▼
┌─────────────────────────────────────────────────────┐
│             RETRIEVAL PIPELINE                      │
│  Query Expansion → Hybrid Search (BM25 + Dense)     │
│  → RRF Merge → Cross-Encoder Re-rank                │
│  → Answer (Opus streaming)                          │
└──────────┬────────────────────┬────────────────────┘
           │ REST/SSE           │ stdio / HTTP+SSE
           ▼                    ▼
┌──────────────────┐   ┌───────────────────────┐
│  Next.js UI      │   │  MCP Server            │
│  (port 3000)     │   │  (Claude Desktop)      │
└──────────────────┘   └───────────────────────┘
```

There are two independent paths to query the data:
- **Web UI path**: Browser → Next.js → FastAPI → Retrieval pipeline
- **Claude Desktop path**: Claude app → MCP stdio → Retrieval pipeline

---

## 2. Repository Layout

```
pascoask/
├── config.py                   # All settings (env vars / .env)
├── pyproject.toml              # Python package definition
│
├── ingestion/
│   ├── models.py               # Document and Chunk dataclasses
│   ├── pipeline.py             # CLI orchestrator (run scrapers, chunk, enrich, embed)
│   ├── chunker.py              # Splits documents into retrieval chunks
│   ├── enricher.py             # LLM (Haiku) tagging of each chunk
│   └── scrapers/
│       ├── base.py             # Abstract BaseScraper with HTTP + retry + rate limit
│       ├── bcc_minutes.py      # Board of County Commissioners meetings
│       ├── civicclerk.py       # All other CivicClerk events (Planning, MPO, etc.)
│       └── ldc.py              # Land Development Code amendment PDFs
│
├── storage/
│   ├── db.py                   # SQLite helpers (schema, upsert, queries)
│   └── vector_store.py         # fastembed + Qdrant: embed, index, dense search
│
├── retrieval/
│   ├── pipeline.py             # Full RAG pipeline: expand→search→rerank→answer
│   ├── hybrid_search.py        # BM25 (in-memory) + dense (Qdrant), RRF merge
│   ├── query_expansion.py      # LLM generates alternative query phrasings
│   └── reranker.py             # Cross-encoder re-scoring (sentence-transformers)
│
├── api/
│   └── main.py                 # FastAPI app: POST /chat (SSE stream), GET /health
│
├── mcp_server/
│   ├── server.py               # MCP server (stdio + HTTP/SSE transports)
│   └── tools.py                # Tool implementations calling retrieval pipeline
│
├── ui/                         # Next.js 14 frontend
│   ├── src/app/page.tsx        # Main page: state management + SSE fetch
│   ├── src/components/
│   │   ├── ChatPanel.tsx       # Message thread + input form
│   │   └── SourcesSidebar.tsx  # Source citations panel
│   └── src/types/index.ts      # TypeScript types
│
├── scripts/
│   ├── diagnose.py             # API probe / debug tool
│   └── query.py                # CLI to query the RAG pipeline directly
│
└── eval/
    ├── golden_qa.json          # Hand-written Q&A pairs for evaluation
    └── run_eval.py             # Automated evaluation script
```

---

## 3. Configuration

**File:** `config.py`

Uses **Pydantic Settings** to load config from environment variables or a `.env` file. Every other module imports the single `settings` singleton.

```python
from config import settings
settings.anthropic_api_key   # str
settings.qdrant_url          # str, default "http://localhost:6333"
settings.sqlite_db_path      # Path, default "./data/pascoask.db"
settings.enrichment_model    # "claude-haiku-4-5-20251001" (cheap batch)
settings.answer_model        # "claude-opus-4-6" (quality answers)
settings.request_delay_seconds  # 1.5s between HTTP requests
```

**Why Pydantic Settings?** It validates types at startup and reads from `.env` automatically — no manual `os.environ.get()` calls scattered throughout the codebase.

---

## 4. Data Models

**File:** `ingestion/models.py`

Two dataclasses form the core data contract:

### `Document`
Represents a single scraped government record — a whole meeting minutes PDF, an LDC section, etc.

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | Stable unique key e.g. `bcc_minutes_2026_03_10` |
| `source` | str | Which scraper: `bcc_minutes`, `civicclerk`, `ldc` |
| `doc_type` | str | `meeting_minutes`, `ordinance`, `staff_report` |
| `title` | str | Human-readable title |
| `body` | str | Full text content |
| `date` | str | ISO 8601 date string |
| `url` | str | Canonical source URL |
| `metadata` | dict | Source-specific extras (event_id, agenda_items, etc.) |

### `Chunk`
A retrieval-sized slice of a Document, ready for embedding.

| Field | Type | Description |
|-------|------|-------------|
| `id` | str | `{doc_id}_chunk_{n}` |
| `document_id` | str | FK back to the parent Document |
| `text` | str | The actual text that gets embedded |
| `summary` | str | LLM-generated 1-2 sentence summary (filled by enricher) |
| `entity_tags` | list[str] | Extracted entities: names, dates, zoning codes, etc. |
| `citizen_question` | str | "The question this chunk answers" (for BM25 boost) |
| `enriched_at` | str | Timestamp set when Haiku processes it |
| `embedded_at` | str | Timestamp set when Qdrant indexes it |

**Key insight:** Chunks carry `enriched_at` and `embedded_at` null timestamps so the pipeline can resume from exactly where it stopped — scraping 500 events can take hours, and you don't want to restart from zero.

---

## 5. Storage Layer

**File:** `storage/db.py`

Uses **sqlite-utils** — a thin wrapper over Python's built-in `sqlite3` that adds nice helpers for schema creation, upserts, and queries.

### Tables

**`documents`** — one row per scraped record
```sql
CREATE TABLE documents (
    id           TEXT PRIMARY KEY,
    source       TEXT,
    doc_type     TEXT,
    title        TEXT,
    body         TEXT,         -- full text
    date         TEXT,
    url          TEXT,
    metadata_json TEXT,        -- JSON blob
    scraped_at   TEXT
)
```

**`chunks`** — one row per chunk, with enrichment fields
```sql
CREATE TABLE chunks (
    id               TEXT PRIMARY KEY,  -- {doc_id}_chunk_{n}
    document_id      TEXT REFERENCES documents(id),
    source           TEXT,
    doc_type         TEXT,
    chunk_index      INTEGER,
    text             TEXT,
    summary          TEXT,              -- filled by enricher
    entity_tags      TEXT,              -- JSON array, filled by enricher
    citizen_question TEXT,              -- filled by enricher
    date             TEXT,
    url              TEXT,
    metadata_json    TEXT,
    enriched_at      TEXT,              -- NULL until enrichment runs
    embedded_at      TEXT               -- NULL until embedding runs
)
```

**`scrape_log`** — one row per URL, for resumability
```sql
CREATE TABLE scrape_log (
    url        TEXT PRIMARY KEY,
    source     TEXT,
    status     TEXT,   -- success | error | skipped
    scraped_at TEXT,
    error      TEXT
)
```

### Key functions

| Function | What it does |
|----------|-------------|
| `upsert_document(db, doc)` | INSERT OR REPLACE a document row |
| `upsert_chunk(db, chunk)` | INSERT OR REPLACE a chunk row |
| `already_scraped(db, url)` | Returns True if scrape_log has `status=success` for this URL |
| `log_scrape(db, url, ...)` | Write a scrape_log entry |
| `get_unenriched_chunks(db)` | SELECT chunks WHERE enriched_at IS NULL |
| `get_unembedded_chunks(db)` | SELECT chunks WHERE enriched_at IS NOT NULL AND embedded_at IS NULL |
| `stats(db)` | Count totals for documents, chunks, enriched, embedded |

**Important SQLite gotcha:** Python's `sqlite3` does NOT auto-commit DML statements (INSERT/UPDATE/DELETE). The `sqlite_utils` library's `.upsert()` method handles commits internally, but raw `db.execute("UPDATE ...")` calls require an explicit `db.conn.commit()` afterward — learned the hard way in the enricher.

---

## 6. Ingestion Pipeline

The ingestion pipeline converts raw government data into searchable vector chunks. It runs in four sequential stages:

```
Web / API → Scrape → Chunk → Enrich → Embed
                ↓        ↓       ↓        ↓
              SQLite  SQLite  SQLite   Qdrant
```

Each stage is idempotent and resumable — re-running a stage only processes records that haven't been processed yet.

---

### 6a. Base Scraper

**File:** `ingestion/scrapers/base.py`

`BaseScraper` is an abstract class that all three scrapers inherit from. It handles:

**HTTP client management:**
```python
self._client = httpx.AsyncClient(
    headers={"User-Agent": "PascoAsk/0.1 ..."},
    timeout=httpx.Timeout(30.0, connect=10.0),
    follow_redirects=True,
)
```

**Rate limiting:** Every request goes through `_rate_limited_get()`, which checks how long since the last request and sleeps if needed (default: 1.5s between requests). This is polite scraping — it doesn't hammer government servers.

**Retry logic:** The `@retry` decorator on `_fetch_with_retry()` uses **exponential backoff** — waits 2s, then 4s, then 8s, up to 4 attempts — but only for network-level errors (`httpx.HTTPError`, timeouts). It does NOT retry 404s or other 4xx responses (those are permanent and retrying them wastes time).

**Resumability via `scrape_log`:** Before calling `scrape_one()`, the base `run()` method checks `already_scraped(db, url)`. If the URL was previously scraped successfully, it skips it. The `--force` flag bypasses this check.

**The `run()` flow:**
```
discover_urls() ─► for each url:
    already_scraped? → skip
    scrape_one(url) → Document or None
    upsert_document() → SQLite
    log_scrape() → scrape_log
```

**Subclasses must implement:**
- `discover_urls()` — async generator that yields URLs
- `scrape_one(url)` — fetches one URL and returns a `Document` (or `None` to skip)

---

### 6b. BCC Minutes Scraper

**File:** `ingestion/scrapers/bcc_minutes.py`

**Data source:** CivicClerk OData API at `https://pascocofl.api.civicclerk.com/v1`

**Why OData?** The Pasco County website links to a CivicClerk portal (a React SPA). There's no traditional HTML to scrape. By examining the JavaScript bundle, we found the real REST API endpoint that the portal itself calls. OData is a REST standard with URL-based query syntax.

**How `discover_urls()` works:**

1. Fetches paginated events from the API with date filters:
   ```
   GET /Events?$filter=eventDate ge 2020-01-01T00:00:00Z and eventDate le {today}
              &$orderby=eventDate desc
              &$top=50
   ```
2. Filters to only events whose name contains "board of county commissioners"
3. Skips events with no `publishedFiles` (future events, or events with no documents)
4. **Caches the full event dict** in `self._event_cache[portal_url]` — this is critical because the API's per-event endpoint (`GET /Events/{id}`) does NOT include `publishedFiles`, only the paged list response does
5. Yields `https://pascocofl.portal.civicclerk.com/event/{id}` as the canonical URL

**How `scrape_one()` works:**

1. Retrieves the cached event dict (avoids a second API call)
2. Sorts `publishedFiles` — Minutes files get priority over Agenda files
3. Tries to download each file's PDF:
   - First tries the CDN URL: `https://civicclerkcdn.azureedge.net/publicportal-live/{path}`
   - Then tries the API URL: `https://pascocofl.api.civicclerk.com/v1/{path}`
4. **PDF downloads use the raw HTTP client directly** (no retry) because these CDN 404s are permanent — the CDN requires auth tokens that rotate. Retrying them with exponential backoff would take 2+ minutes per file for no benefit.
5. If no PDF is accessible, **falls back to a metadata-only document** built from `_build_metadata_body(event)` — this constructs a text body from the event's name, date, location, category, and published file titles. It's less rich than a full PDF but unblocks the rest of the pipeline.

**PDF text extraction:** Uses **pdfplumber** with `x_tolerance=2, y_tolerance=3` for better column alignment. PDFs are cached to disk by MD5 hash of their URL so reruns don't re-download.

---

### 6c. CivicClerk Scraper

**File:** `ingestion/scrapers/civicclerk.py`

Same OData API as the BCC scraper, but covers **all non-BCC events**: Planning Commission, MPO (Metropolitan Planning Organization), workshops, special sessions, etc.

Key difference from BCC scraper:
- Filters OUT events whose name contains "board of county commissioners" (those are handled by the BCC scraper)
- Uses a simpler PDF selection (no Minutes/Agenda priority sorting)
- Document IDs are `civicclerk_event_{id}` rather than date-based

Both scrapers import shared constants and helpers from `bcc_minutes.py`:
```python
from ingestion.scrapers.bcc_minutes import (
    API_BASE, CDN_BASE, PORTAL_BASE, BCC_KEYWORDS, DATE_FROM,
    _parse_event_date, _build_title, _build_metadata_body,
)
```

---

### 6d. LDC Scraper

**File:** `ingestion/scrapers/ldc.py`

**Data source:** Pasco County's Land Development Code amendment page — a traditional HTML page with links to PDFs.

**How it works:**
1. Fetches the amendments page HTML
2. Parses with BeautifulSoup, finds all `<a>` tags ending in `.pdf`
3. For each PDF link, downloads and extracts text with pdfplumber
4. Uses regex to extract the LDC section number from the filename or PDF content
5. Document IDs are `ldc_{section_number}`

The LDC scraper uses `_rate_limited_get()` (with retry) because this is a regular web server that can have transient errors, unlike the CivicClerk CDN.

---

### 6e. Chunker

**File:** `ingestion/chunker.py`

Splits a full `Document` into `Chunk` objects sized for embedding and retrieval. Chunk size matters: too small loses context, too large dilutes relevance.

**Hard limits:**
- `MIN_CHUNK_CHARS = 80` — discard tiny fragments (table headers, page numbers)
- `MAX_CHUNK_CHARS = 4000` — hard split oversized chunks at sentence boundaries

**Per-source strategies:**

| Source | Strategy | Why |
|--------|----------|-----|
| `bcc_minutes` | Split by agenda item number (regex: `\d{1,2}[A-Z]?\s*[\.\-\)]\s*`) | Each agenda item is a distinct topic with its own vote |
| `ldc` | Split by `Section X.X` / `ARTICLE` headers | LDC is structured by numbered sections |
| `civicclerk` | Split by ALL-CAPS headings or `## Heading` lines | Staff reports have formal section headers |
| `gis` | Single chunk per document | GIS records are already atomic |
| default | Split on blank lines (paragraphs) | Generic fallback |

**The `_hard_split()` helper:** If a section exceeds `MAX_CHUNK_CHARS`, it splits at sentence boundaries (`.`, `!`, `?` followed by whitespace) rather than mid-sentence.

**Chunk IDs:** `{doc_id}_chunk_{index}` — deterministic and stable, so re-chunking the same document produces the same IDs.

---

### 6f. Enricher

**File:** `ingestion/enricher.py`

Calls **Claude Haiku** (cheap, fast) to add three fields to each chunk:

| Field | Description | Example |
|-------|-------------|---------|
| `summary` | 1-2 sentence plain-English summary | "The BCC approved a rezoning application for 42 acres on SR-54 from AR to MPUD." |
| `entity_tags` | Extracted named entities | `["SR-54", "MPUD", "42 acres", "2026-03-10", "$1.2M"]` |
| `citizen_question` | The question this chunk answers | "What zoning changes were approved on SR-54?" |

**Why enrich?**
- The `summary` becomes the text that gets **embedded into Qdrant** (cleaner signal than raw text)
- `entity_tags` boosts **BM25 keyword search** precision
- `citizen_question` helps match user queries to relevant chunks even when wording differs

**Batch processing:** Processes chunks one at a time but in a loop (batch size 20). Each chunk gets its own LLM call. After each successful call, `db.conn.commit()` is called explicitly to persist the result — without this, Python's sqlite3 buffers the UPDATE in an uncommitted transaction and rolls it back when the connection closes.

**Retry logic:** The `@retry` decorator retries on any exception (rate limits, timeouts, parse errors) with exponential backoff. If the LLM returns text that can't be parsed as JSON, the chunk is logged as an error and skipped — the pipeline continues.

**Model used:** `claude-haiku-4-5-20251001` — about 15x cheaper than Opus, fast enough for batch processing thousands of chunks.

---

### 6g. Pipeline Orchestrator

**File:** `ingestion/pipeline.py`

CLI entry point that sequences the four stages:

```bash
python -m ingestion.pipeline --scrapers bcc_minutes civicclerk --chunk --enrich --embed
```

**Flags:**

| Flag | Effect |
|------|--------|
| `--scrapers bcc_minutes ldc civicclerk` | Which scrapers to run |
| `--scrapers all` | Run all three scrapers |
| `--force` | Re-scrape URLs already in scrape_log |
| `--chunk` | Run chunking after scraping |
| `--enrich` | Run LLM enrichment (needs Anthropic key) |
| `--embed` | Run vector embedding + Qdrant indexing (needs Qdrant) |
| `--max-events N` | Limit scraping to N events (for testing) |
| `--max-chunks N` | Limit enrichment/embedding to N chunks (for testing cheaply) |

**Stages can run independently:** You can run `--chunk` without `--scrapers` to chunk documents already in the DB. Same for `--enrich` and `--embed`. This allows incremental processing:

```bash
# Day 1: scrape everything
python -m ingestion.pipeline --scrapers all

# Day 2: chunk + enrich new documents
python -m ingestion.pipeline --chunk --enrich

# Day 3: embed new enriched chunks
python -m ingestion.pipeline --embed
```

---

## 7. Vector Store

**File:** `storage/vector_store.py`

Handles converting chunk text into dense vectors and storing/querying them in Qdrant.

### Embedding Model

**fastembed** with `BAAI/bge-small-en-v1.5`:
- Runs **locally** — no API key, no per-token cost
- Uses ONNX Runtime for fast CPU inference
- Produces **384-dimensional** vectors
- Model files (~22MB) download from HuggingFace on first use and cache locally

```python
from fastembed import TextEmbedding
embedder = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
vectors = [v.tolist() for v in embedder.embed(texts)]
```

**Why not OpenAI embeddings?** OpenAI's `text-embedding-3-small` (1536-dim) requires a paid API key. fastembed gives good quality embeddings at zero ongoing cost. The tradeoff is slightly lower quality on complex semantic queries.

### Qdrant Collection

Qdrant is a purpose-built vector database. The collection `pasco_chunks` is configured with:
- **Cosine similarity** distance (appropriate for normalized embeddings)
- **Payload indexes** on `source`, `doc_type`, `date`, `document_id` for filtered search

```python
client.create_collection(
    collection_name="pasco_chunks",
    vectors_config=VectorParams(size=384, distance=Distance.COSINE),
)
```

### `embed_and_index()`

1. Calls `get_unembedded_chunks(db)` — only processes chunks with `enriched_at IS NOT NULL AND embedded_at IS NULL`
2. For each batch of 100 chunks: embeds the `summary` field (falls back to raw `text` if not enriched)
3. Upserts into Qdrant as `PointStruct` objects — each point has a stable integer ID (MD5 hash of chunk_id) plus the full chunk payload
4. Updates `embedded_at` in SQLite

### `dense_search()`

```python
query_vector = _embed_texts([query])[0]
results = qdrant.query_points(
    collection_name="pasco_chunks",
    query=query_vector,
    limit=top_k,
    query_filter=...,  # optional source/date filters
    with_payload=True,
)
```

Returns the top-k most semantically similar chunks as dicts with a `_score` field (0–1 cosine similarity).

---

## 8. Retrieval Pipeline

**File:** `retrieval/pipeline.py`

The query time pipeline has four stages:

```
User Question
     │
     ▼ (1) Query Expansion
  3 alternative phrasings (Opus LLM)
     │
     ▼ (2) Hybrid Search — for each phrasing, in parallel:
  Dense search (Qdrant)  +  BM25 search (in-memory SQLite corpus)
     │
     ▼ (3) RRF Merge
  Reciprocal Rank Fusion combines all result lists → top 20 candidates
     │
     ▼ (4) Cross-Encoder Re-ranking
  ms-marco-MiniLM model scores each candidate → top 5
     │
     ▼ (5) Answer Generation
  Claude Opus with source context → streaming response
```

### 8a. Query Expansion

**File:** `retrieval/query_expansion.py`

Calls Claude Opus to generate 2–3 alternative phrasings of the user's question. This compensates for vocabulary mismatches between how citizens ask questions and how government documents are written.

**Example:**
- Input: `"Can I build a fence in my front yard?"`
- Output: `["Can I build a fence in my front yard?", "residential fence setback requirements Pasco County", "fence permit front yard zoning code"]`

All phrasings are searched in parallel. This significantly improves recall.

**Model used:** `claude-opus-4-6` — uses the most capable model here because query quality directly affects answer quality.

---

### 8b. Hybrid Search

**File:** `retrieval/hybrid_search.py`

Combines two search methods, each with different strengths:

**Dense search (semantic):** Understands meaning. "What permits do I need for a shed?" finds chunks about "accessory structure permits" even if the word "shed" never appears.

**BM25 search (keyword):** Finds exact matches. "Ordinance 2021-14" will only be found reliably by a keyword search — a semantic search might return unrelated ordinances.

**How BM25 works here:**
- The corpus is all enriched chunks loaded from SQLite into memory (a Python list of dicts)
- BM25 is a classic TF-IDF variant that scores documents by how often query terms appear, normalized by document length
- Parameters: `k1=1.5` (term frequency saturation), `b=0.75` (length normalization)
- Runs completely in-memory — no external service needed

**Parallelism:** All search calls (dense + BM25, for each query expansion) run simultaneously in a `ThreadPoolExecutor`. For 3 expanded queries, that's up to 6 parallel searches.

**Reciprocal Rank Fusion (RRF):** Merges multiple ranked lists into one. For each result, its RRF score is `1 / (60 + rank)` summed across all lists. The constant `60` prevents small rank differences from dominating. Results appearing at rank 1 in multiple lists score highest.

```python
# If "Meeting on March 10" appears at rank 2 in dense and rank 1 in BM25:
rrf_score = 1/(60+2) + 1/(60+1) = 0.0161 + 0.0164 = 0.0325
```

---

### 8c. Re-ranker

**File:** `retrieval/reranker.py`

Takes the top 10 RRF results and re-scores them with a **cross-encoder** model:

```
cross-encoder/ms-marco-MiniLM-L-6-v2 (sentence-transformers)
```

**Why re-rank?** Embedding models encode the query and each chunk separately, then compare. A cross-encoder sees the query AND the chunk together — it's much more accurate at judging relevance but much slower (can't pre-index chunks).

The re-ranker is only applied to the top 10 candidates (fast enough) to get the best top 5.

**First-run note:** On first call, the model downloads from HuggingFace (~80MB). This happens inside the API server process on the first user request, causing a delay of 30–60 seconds. After that, it's cached.

**Lazy loading with `@lru_cache`:** The model is loaded once and reused:
```python
@lru_cache(maxsize=1)
def _get_cross_encoder():
    return CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
```

---

### 8d. Answer Generation & Streaming

**File:** `retrieval/pipeline.py`

The `query_stream()` generator (used by the API) and `query()` function (used by MCP tools) both call Claude Opus with a structured prompt:

```
System: You are PascoAsk. Use ONLY the provided source documents. Cite sources inline as [Source N].

User: Question: {user_question}

Source documents:
[Source 1] (bcc_minutes | meeting_minutes | 2026-03-10)
URL: https://pascocofl.portal.civicclerk.com/event/1863
Meeting: Board of County Commissioners
Date: 2026-03-10
...
```

**Streaming:** `query_stream()` is a Python generator. It uses Claude's `.messages.stream()` context manager, which yields text tokens as they arrive. Each token is yielded as a `("token", text)` tuple, which the API converts to an SSE event.

```python
with client.messages.stream(...) as stream:
    for text in stream.text_stream:
        yield "token", text
```

The sources are emitted first (before streaming starts) so the UI can show citations immediately.

---

## 9. FastAPI Backend

**File:** `api/main.py`

A minimal **FastAPI** app with two endpoints:

### `GET /health`
Returns `{"status": "ok"}` — used to check if the server is running.

### `POST /chat`
Accepts a JSON body:
```json
{
  "message": "Tell me about recent commissioner meetings",
  "history": [],
  "doc_type": null,
  "source": null,
  "date_from": null,
  "date_to": null
}
```

Returns a **Server-Sent Events (SSE) stream** using `StreamingResponse`:

```
data: {"type": "sources", "chunks": [...]}

data: {"type": "token", "content": "The Board of County"}

data: {"type": "token", "content": " Commissioners held"}

data: {"type": "done"}
```

**SSE format:** Each line starts with `data: `, followed by JSON. Events are separated by double newlines (`\n\n`). This is the standard SSE format that browsers understand natively via `EventSource` or the Fetch API's readable stream.

**CORS:** The middleware allows all origins (`*`) so the Next.js dev server on port 3000 can call the API on port 8000.

**How to start:**
```bash
python -m uvicorn api.main:app --reload
# Runs on http://localhost:8000
```

**uvicorn** is an ASGI server (Asynchronous Server Gateway Interface) — the standard way to run FastAPI/Starlette apps. `--reload` watches for file changes and restarts automatically during development.

---

## 10. Next.js Frontend

**Directory:** `ui/`

A **Next.js 14** app using the App Router, React hooks, and Tailwind CSS.

### Page structure (`src/app/page.tsx`)

The main page is a client component (`"use client"`) that owns all state:

```
page.tsx  (state owner)
├── <header>          Header with PascoAsk title + search mode toggle
├── <ChatPanel>       Message thread + input form
└── <SourcesSidebar>  Source citations panel (right side)
```

**State managed in `page.tsx`:**
- `messages` — array of `{id, role, content, sources}` objects
- `input` — current text in the input field
- `isLoading` — true while waiting for API response
- `activeSources` — source chunks from the last query (shown in sidebar)
- `plainEnglishModal` — open/closed state + content for the "Plain English" popup

### How a chat message works (`handleSubmit`)

```
1. User submits form
2. Append user message to messages[]
3. Clear input, set isLoading=true
4. POST to http://localhost:8000/chat (fetch API)
5. Append empty assistant message to messages[]
6. Read the SSE stream byte-by-byte:
   - "sources" event → update activeSources + message.sources
   - "token" event  → append to assistantContent, update message.content
   - done/EOF       → set isLoading=false
```

**Why read the stream manually?** The Vercel AI SDK could abstract this, but reading `response.body.getReader()` directly gives full control over the SSE parsing and is simpler to understand.

### SSE parsing

```typescript
const reader = response.body!.getReader();
const decoder = new TextDecoder();

while (true) {
  const { done, value } = await reader.read();
  if (done) break;

  const text = decoder.decode(value);
  const lines = text.split("\n").filter(l => l.startsWith("data: "));

  for (const line of lines) {
    const event = JSON.parse(line.slice(6));  // strip "data: "
    if (event.type === "token") {
      assistantContent += event.content;
      // update React state with each new token
    }
  }
}
```

This creates the **streaming typewriter effect** — the response appears word-by-word as Claude generates it, not all at once.

### `ChatPanel` component

Renders the message thread. Each message is a rounded bubble, right-aligned (blue) for user, left-aligned (white) for assistant. Uses `clsx` for conditional CSS classes.

### `SourcesSidebar` component

Shows source cards for the most recent answer. Each card has:
- Source type badge (color-coded by `source` field)
- Document title + date
- Snippet of the chunk text
- "Plain English" button → calls `/chat` with a simplification prompt and shows result in a modal

### API URL

```typescript
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
```

Setting `NEXT_PUBLIC_API_URL` in `.env.local` lets you point the UI at a deployed API without code changes.

### How to start:
```bash
cd ui/
npm install
npm run dev
# Runs on http://localhost:3000
```

---

## 11. MCP Server

**File:** `mcp_server/server.py`

The **Model Context Protocol (MCP)** is Anthropic's open standard for giving AI models access to external tools. PascoAsk implements an MCP server so that Claude Desktop (or any MCP-compatible client) can search Pasco County records directly from a conversation.

### What MCP is

When you chat with Claude Desktop and it has an MCP server configured, Claude can decide to call a tool mid-conversation. The tool runs, returns data, and Claude incorporates it into its response — all transparently.

### The 5 tools

| Tool | Input | What it does |
|------|-------|-------------|
| `search_pasco_records` | `query`, optional `doc_type`, `date_from`, `date_to` | Full RAG search — expand, hybrid search, rerank, return top chunks |
| `get_zoning_rules` | `zoning_code` | Search specifically for zoning code rules |
| `get_meeting_item` | `meeting_date`, `item_number` | Find a specific agenda item and its vote |
| `find_commissioner_votes` | `topic`, optional `commissioner` | Search for voting records on a topic |
| `summarize_topic` | `topic`, optional `date_range` | Retrieve and summarize all records on a topic |

### Two transports

**stdio transport** (for Claude Desktop):
```
Claude Desktop → spawn process → stdin/stdout JSON-RPC → MCP server
```
The server reads from stdin and writes to stdout. Claude Desktop launches it as a subprocess.

Config goes in `%APPDATA%\Claude\claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "pascoask": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "C:/path/to/pascoask"
    }
  }
}
```

**HTTP/SSE transport** (for web clients):
```bash
python -m mcp_server.server --http --port 8001
```
Runs a Starlette web server. Clients connect via GET `/sse` (SSE stream for server→client) and POST `/messages/` (client→server).

### How a tool call flows

```
Claude Desktop
    │ "What did commissioners decide about SR-54?"
    ▼
Claude sees it needs data → calls search_pasco_records("SR-54 commissioner decision")
    │
    ▼ stdin
MCP Server
    │ calls pasco_tools.search_pasco_records(query="SR-54 commissioner decision")
    │
    ▼
retrieval/pipeline.py query()
    │ expand → hybrid search → rerank → answer
    ▼
Returns JSON: {"answer": "...", "sources": [...]}
    │
    ▼ stdout
Claude Desktop
    │ incorporates tool result into response
    ▼
User sees answer with citations
```

---

## 12. End-to-End Request Flow

Here is the complete sequence of function calls when a user types "Where do BCC meetings take place?" in the web UI:

```
Browser
  └─ handleSubmit() in page.tsx
       └─ fetch("POST http://localhost:8000/chat", {message: "Where do BCC meetings take place?"})

FastAPI (api/main.py)
  └─ chat() endpoint receives ChatRequest
       └─ StreamingResponse(_generate())
            └─ query_stream(question="Where do BCC meetings take place?")

retrieval/pipeline.py
  └─ query_stream()
       │
       ├─ (1) expand_query("Where do BCC meetings take place?")
       │       └─ anthropic.messages.create(model="claude-opus-4-6", ...)
       │           returns: ["Where do BCC meetings take place?",
       │                     "Pasco County BCC meeting location address",
       │                     "Board of County Commissioners meeting venue"]
       │
       ├─ (2) _load_corpus() → SQLite SELECT enriched chunks → list of dicts
       │
       ├─ (3) hybrid_search(queries=[3 phrasings], corpus=chunks)
       │       retrieval/hybrid_search.py
       │       └─ ThreadPoolExecutor (6 parallel searches):
       │            ├─ dense_search("Where do BCC meetings...") → Qdrant query_points()
       │            ├─ dense_search("Pasco County BCC meeting location...")
       │            ├─ dense_search("Board of County Commissioners meeting venue")
       │            ├─ bm25_search("Where do BCC meetings...") → in-memory scoring
       │            ├─ bm25_search("Pasco County BCC meeting location...")
       │            └─ bm25_search("Board of County Commissioners meeting venue")
       │       └─ _rrf_merge() × 2 → top 20 candidates
       │
       ├─ (4) rerank("Where do BCC meetings take place?", candidates[:10], top_k=5)
       │       retrieval/reranker.py
       │       └─ CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2").predict(pairs)
       │           returns top 5 re-scored chunks
       │
       ├─ (5) yield "sources", [dataclasses.asdict(s) for s in sources]
       │       → SSE: data: {"type": "sources", "chunks": [...]}
       │
       └─ (6) client.messages.stream(model="claude-opus-4-6", messages=[context])
               for text in stream.text_stream:
                   yield "token", text
                   → SSE: data: {"type": "token", "content": "BCC meetings are held..."}

Browser (page.tsx)
  └─ reader.read() loop
       ├─ "sources" event → setActiveSources() → SourcesSidebar re-renders
       └─ "token" events → append to assistantContent → ChatPanel re-renders each token
```

**Total latency breakdown (approximate):**
- Query expansion: ~1–2s (Opus LLM call)
- Parallel search: ~200–500ms (Qdrant + in-memory BM25)
- Re-ranking: ~100–300ms (local ONNX model)
- Time to first token: ~2–4s total
- Full answer streaming: 5–15s depending on answer length

---

## 13. How to Run Everything

### Prerequisites
```bash
pip install -e .          # Install Python package
cd ui && npm install      # Install Node dependencies
```

### Environment variables (`.env`)
```
ANTHROPIC_API_KEY=sk-ant-...
QDRANT_URL=https://your-cluster.qdrant.io:6333
QDRANT_API_KEY=your-qdrant-key
QDRANT_COLLECTION=pasco_chunks
```

### Step 1: Ingest data
```bash
# Scrape 10 events for a quick test
python -m ingestion.pipeline --scrapers bcc_minutes --max-events 10 --chunk --enrich --embed

# Full ingest (takes 30+ minutes for 200+ events)
python -m ingestion.pipeline --scrapers all --chunk --enrich --embed
```

### Step 2: Start the API
```bash
python -m uvicorn api.main:app --reload
# → http://localhost:8000
```

### Step 3: Start the UI
```bash
cd ui
npm run dev
# → http://localhost:3000
```

### Step 4 (optional): MCP server for Claude Desktop
```bash
python -m mcp_server.server
# Runs on stdio, configure in Claude Desktop settings
```

### Query via CLI (no UI needed)
```bash
python scripts/query.py "When is the next BCC meeting?"
```

---

## 14. Key Design Decisions & Trade-offs

### Why SQLite instead of PostgreSQL?
SQLite requires zero setup and is file-based — perfect for a single-developer local tool. The ingestion pipeline is not concurrent (one scraper runs at a time), so SQLite's single-writer limitation is not an issue. If this were a multi-user production system, PostgreSQL would be better.

### Why fastembed instead of OpenAI embeddings?
OpenAI's `text-embedding-3-small` is higher quality but costs money and requires an API key. fastembed with `BAAI/bge-small-en-v1.5` is free, runs locally, and is good enough for this domain. The trade-off is slightly lower semantic accuracy on complex queries.

### Why metadata fallback when PDFs aren't accessible?
The CivicClerk CDN requires rotating auth tokens — we can't download PDFs without authenticating as a portal user. Rather than returning zero results, we build documents from the structured event metadata (name, date, location, category). This gives partial value immediately, and PDF access can be added later if auth tokens are obtained.

### Why BM25 in-memory instead of a dedicated keyword index?
For a corpus of a few thousand chunks, loading all chunk text into memory (~10MB) and running BM25 in Python is fast enough (~50ms). Adding Elasticsearch or another keyword search engine would be over-engineering for this scale.

### Why cross-encoder re-ranking as a separate stage?
Cross-encoders are accurate but slow — you can't index millions of chunks against them. The pattern of "retrieve many with fast methods, then re-rank a small subset with an accurate model" is standard in production RAG systems. Here we retrieve 20 and re-rank to 5.

### Why stream the answer token-by-token?
Full-answer latency with Opus can be 10–20 seconds. Streaming makes it feel responsive — users see the answer building immediately rather than staring at a spinner. The SSE protocol is the right tool: it's simple, supported natively by browsers, and works over plain HTTP.

### Why separate the enrichment and embedding steps?
Enrichment costs money (LLM API calls). Embedding costs time (model inference). By keeping them separate with the `enriched_at` / `embedded_at` null-check pattern, you can:
- Enrich a small batch first (`--max-chunks 20`) to validate quality cheaply
- Re-embed without re-enriching if you change the embedding model
- Resume interrupted runs without reprocessing already-done chunks
