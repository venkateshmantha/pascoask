# PascoAsk 🏛️

**Natural language agent over Pasco County, FL public government records.**

Ask questions like:
- *"What did the commissioners decide about the SR-54 corridor development?"*
- *"What are the setback requirements for a residential fence?"*
- *"Has there been any discussion about flood zone changes near Zephyrhills?"*

...and get grounded, cited answers linked directly to the source government documents.

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

## Tech Stack

| Layer | Technology |
|---|---|
| LLM (enrichment + answers) | `claude-opus-4-6` |
| Embeddings | `text-embedding-3-small` (OpenAI) |
| Vector DB | Qdrant (Docker local / Qdrant Cloud) |
| Re-ranker | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| PDF extraction | `pdfplumber` |
| MCP server | Python MCP SDK |
| API | FastAPI |
| Frontend | Next.js + Vercel AI SDK |
| Eval | RAGAS |
| Raw storage | SQLite via `sqlite-utils` |

---

## Quick Start

### Prerequisites
- Python 3.11+
- Docker (for Qdrant)
- API keys: Anthropic, OpenAI

### Setup

```bash
git clone https://github.com/YOUR_USERNAME/pascoask.git
cd pascoask

# Create virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e ".[dev]"

# Configure environment
cp .env.example .env
# Edit .env and add your API keys

# Start Qdrant locally
docker run -p 6333:6333 qdrant/qdrant
```

### Run the ingestion pipeline

```bash
# Scrape BCC meeting minutes (Milestone 1)
python -m ingestion.pipeline --scrapers bcc_minutes

# Check what was collected
python scripts/db_stats.py

# Scrape all sources (Milestone 2+)
python -m ingestion.pipeline --scrapers all
```

---

## Build Milestones

- [x] **M1** — Project scaffold + BCC minutes scraper
- [ ] **M2** — LDC + CivicClerk scrapers
- [ ] **M3** — Chunking pipeline (per-source strategies)
- [ ] **M4** — LLM enrichment (claude-opus-4-6)
- [ ] **M5** — Qdrant embedding + indexing
- [ ] **M6** — RAG query pipeline (hybrid search + re-ranking)
- [ ] **M7** — MCP server (5 tools)
- [ ] **M8** — FastAPI backend + Next.js chat UI

---

## Data Sources

All data is freely available under Florida's Public Records Law (Chapter 119, Florida Statutes).

| Source | Content | URL |
|---|---|---|
| BCC Meeting Minutes | Commission decisions, votes, public hearings | pascocountyfl.gov |
| Land Development Code | Zoning rules, setbacks, permitted uses | pascocountyfl.gov/planning |
| CivicClerk Portal | Agenda items + staff reports | pascocofl.portal.civicclerk.com |
| GIS Data | Zoning boundaries, FEMA zones, land use | pascocountyfl.gov/gis |

---

## Evaluation

The `eval/` directory contains a golden QA dataset of 40 manually verified question-answer pairs drawn from actual county documents. Run the eval harness with:

```bash
python eval/run_eval.py
```

Results are saved to `eval/results/` and tracked in version control.

---

## License

MIT — data sourced from public government records.
