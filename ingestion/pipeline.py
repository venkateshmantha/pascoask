"""
Ingestion pipeline orchestrator.

Usage:
    python -m ingestion.pipeline --scrapers bcc_minutes
    python -m ingestion.pipeline --scrapers all
    python -m ingestion.pipeline --scrapers bcc_minutes --force
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


async def run_scrapers(scraper_names: list[str], force: bool = False) -> None:
    settings.ensure_dirs()
    db = get_db()

    results: dict[str, dict] = {}

    for name in scraper_names:
        if name == "bcc_minutes":
            from ingestion.scrapers.bcc_minutes import run_bcc_scraper
            console.rule(f"[bold blue]Scraper: {name}")
            results[name] = await run_bcc_scraper(db, force=force)

        elif name == "ldc":
            console.print("[yellow]LDC scraper coming in Milestone 2")

        elif name == "civicclerk":
            console.print("[yellow]CivicClerk scraper coming in Milestone 2")

        else:
            console.print(f"[red]Unknown scraper: {name}")

    # Summary table
    db_stats = stats(db)
    table = Table(title="Pipeline Summary", show_header=True)
    table.add_column("Scraper")
    table.add_column("Scraped", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Errors", justify="right")

    for name, r in results.items():
        table.add_row(name, str(r.get("scraped", 0)), str(r.get("skipped", 0)), str(r.get("errors", 0)))

    console.print(table)
    console.print(f"\n[bold]DB totals:[/bold] {db_stats['documents']} documents, {db_stats['chunks']} chunks")


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )

    parser = argparse.ArgumentParser(description="PascoAsk ingestion pipeline")
    parser.add_argument(
        "--scrapers",
        nargs="+",
        default=["bcc_minutes"],
        choices=["bcc_minutes", "ldc", "civicclerk", "all"],
        help="Which scrapers to run",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-scrape even URLs already in the scrape_log",
    )
    args = parser.parse_args()

    scraper_names = args.scrapers
    if "all" in scraper_names:
        scraper_names = ["bcc_minutes", "ldc", "civicclerk"]

    asyncio.run(run_scrapers(scraper_names, force=args.force))


if __name__ == "__main__":
    main()
