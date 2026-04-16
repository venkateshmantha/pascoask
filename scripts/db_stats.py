#!/usr/bin/env python
"""
Quick DB status check.

Usage:
    python scripts/db_stats.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table
from storage.db import get_db, stats

console = Console()


def main():
    db = get_db()
    s = stats(db)

    console.print("\n[bold cyan]PascoAsk — Database Status[/bold cyan]\n")

    table = Table(show_header=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Documents", str(s["documents"]))
    table.add_row("Chunks", str(s["chunks"]))
    table.add_row("Enriched chunks", str(s["enriched_chunks"]))
    table.add_row("Embedded chunks", str(s["embedded_chunks"]))

    for status, count in s["scrape_log"].items():
        table.add_row(f"Scrape log [{status}]", str(count))

    console.print(table)

    # Sample a few documents
    rows = list(db.execute(
        "SELECT id, source, doc_type, date, title FROM documents ORDER BY scraped_at DESC LIMIT 10"
    ).fetchall())

    if rows:
        console.print("\n[bold]Most recent documents:[/bold]")
        doc_table = Table(show_header=True)
        for col in ["id", "source", "doc_type", "date", "title"]:
            doc_table.add_column(col)
        for row in rows:
            doc_table.add_row(*[str(v or "")[:50] for v in row])
        console.print(doc_table)


if __name__ == "__main__":
    main()
