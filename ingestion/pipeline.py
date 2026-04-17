"""
Ingestion pipeline orchestrator.

Usage:
    python -m ingestion.pipeline --scrapers bcc_minutes
    python -m ingestion.pipeline --scrapers ldc civicclerk
    python -m ingestion.pipeline --scrapers all
    python -m ingestion.pipeline --scrapers all --chunk --enrich --embed
    python -m ingestion.pipeline --chunk          # chunk only (no scraping)
    python -m ingestion.pipeline --enrich         # LLM enrichment only
    python -m ingestion.pipeline --embed          # vector embedding only
"""
from __future__ import annotations

import argparse
import asyncio
import logging

from rich.console import Console
from rich.table import Table

from config import settings
from storage.db import get_db, stats

console = Console()
logger = logging.getLogger(__name__)


async def run_scrapers(
    scraper_names: list[str],
    force: bool = False,
    do_chunk: bool = False,
    do_enrich: bool = False,
    do_embed: bool = False,
    max_chunks: int = 0,
    max_events: int = 0,
) -> None:
    settings.ensure_dirs()
    db = get_db()
    results: dict[str, dict] = {}

    for name in scraper_names:
        console.rule(f"[bold blue]Scraper: {name}")
        if name == "bcc_minutes":
            from ingestion.scrapers.bcc_minutes import run_bcc_scraper
            results[name] = await run_bcc_scraper(db, force=force, max_events=max_events)
        elif name == "ldc":
            from ingestion.scrapers.ldc import run_ldc_scraper
            results[name] = await run_ldc_scraper(db, force=force)
        elif name == "civicclerk":
            from ingestion.scrapers.civicclerk import run_civicclerk_scraper
            results[name] = await run_civicclerk_scraper(db, force=force)
        else:
            console.print(f"[red]Unknown scraper: {name}")

    _print_scraper_table(results)

    if do_chunk and scraper_names:
        _run_chunking(db)

    if do_enrich:
        _run_enrichment(db, max_chunks=max_chunks)

    if do_embed:
        _run_embedding(db, max_chunks=max_chunks)

    db_stats = stats(db)
    console.print(
        f"\n[bold]DB totals:[/bold] {db_stats['documents']} documents, "
        f"{db_stats['chunks']} chunks "
        f"({db_stats['enriched_chunks']} enriched, {db_stats['embedded_chunks']} embedded)"
    )


def _run_chunking(db) -> None:
    from ingestion.chunker import chunk_document
    from storage.db import upsert_chunk
    import json

    console.rule("[bold green]Chunking")
    rows = db.execute(
        "SELECT id, source, doc_type, title, body, date, url, metadata_json "
        "FROM documents WHERE id NOT IN (SELECT DISTINCT document_id FROM chunks)"
    ).fetchall()
    console.print(f"Documents to chunk: {len(rows)}")
    total_chunks = 0
    for row in rows:
        meta = json.loads(row[7]) if row[7] else {}
        from ingestion.models import Document
        doc = Document(
            id=row[0], source=row[1], doc_type=row[2], title=row[3],
            body=row[4], date=row[5], url=row[6], metadata=meta,
        )
        chunks = chunk_document(doc)
        for c in chunks:
            upsert_chunk(db, c.to_db_row())
        total_chunks += len(chunks)
    console.print(f"[green]Created {total_chunks} chunks from {len(rows)} new documents")


def _run_enrichment(db, max_chunks: int = 0) -> None:
    from ingestion.enricher import enrich_chunks
    console.rule("[bold yellow]LLM Enrichment")
    if max_chunks:
        console.print(f"[dim]Limiting to {max_chunks} chunks")
    n = enrich_chunks(db, max_chunks=max_chunks)
    console.print(f"[yellow]Enriched {n} chunks")


def _run_embedding(db, max_chunks: int = 0) -> None:
    from storage.vector_store import embed_and_index
    console.rule("[bold magenta]Embedding + Qdrant Indexing")
    if max_chunks:
        console.print(f"[dim]Limiting to {max_chunks} chunks")
    n = embed_and_index(db, max_chunks=max_chunks)
    console.print(f"[magenta]Embedded and indexed {n} chunks")


def _print_scraper_table(results: dict[str, dict]) -> None:
    if not results:
        return
    table = Table(title="Scraper Results", show_header=True)
    table.add_column("Scraper")
    table.add_column("Scraped", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Errors", justify="right")
    for name, r in results.items():
        table.add_row(
            name,
            str(r.get("scraped", 0)),
            str(r.get("skipped", 0)),
            str(r.get("errors", 0)),
        )
    console.print(table)


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    parser = argparse.ArgumentParser(description="PascoAsk ingestion pipeline")
    parser.add_argument(
        "--scrapers",
        nargs="*",
        default=[],
        choices=["bcc_minutes", "ldc", "civicclerk", "all"],
        help="Which scrapers to run",
    )
    parser.add_argument("--force", action="store_true", help="Re-scrape already-logged URLs")
    parser.add_argument("--chunk", action="store_true", help="Run chunking after scraping")
    parser.add_argument("--enrich", action="store_true", help="Run LLM enrichment")
    parser.add_argument("--embed", action="store_true", help="Run embedding + Qdrant indexing")
    parser.add_argument("--max-chunks", type=int, default=0, metavar="N",
                        help="Limit enrichment/embedding to N chunks (0=all). Use to test cheaply.")
    parser.add_argument("--max-events", type=int, default=0, metavar="N",
                        help="Limit scraping to N events per scraper (0=all).")
    args = parser.parse_args()

    scraper_names: list[str] = list(args.scrapers)
    if "all" in scraper_names:
        scraper_names = ["bcc_minutes", "ldc", "civicclerk"]

    # Allow running chunk/enrich/embed without scraping
    if not scraper_names and not any([args.chunk, args.enrich, args.embed]):
        parser.print_help()
        return

    async def _main() -> None:
        settings.ensure_dirs()
        db = get_db()

        if scraper_names:
            await run_scrapers(
                scraper_names,
                force=args.force,
                do_chunk=args.chunk,
                do_enrich=args.enrich,
                do_embed=args.embed,
                max_chunks=args.max_chunks,
                max_events=args.max_events,
            )
        else:
            if args.chunk:
                _run_chunking(db)
            if args.enrich:
                _run_enrichment(db, max_chunks=args.max_chunks)
            if args.embed:
                _run_embedding(db, max_chunks=args.max_chunks)
            db_stats = stats(db)
            console.print(
                f"\n[bold]DB:[/bold] {db_stats['documents']} docs, "
                f"{db_stats['chunks']} chunks, "
                f"{db_stats['enriched_chunks']} enriched, "
                f"{db_stats['embedded_chunks']} embedded"
            )

    asyncio.run(_main())


if __name__ == "__main__":
    main()
