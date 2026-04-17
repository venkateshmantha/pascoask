"""
Query CLI — run a natural language question through the full RAG pipeline.

Usage:
    python scripts/query.py "what are the rules for building a fence?"
    python scripts/query.py "SR-54 corridor" --source bcc_minutes --simple
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Ensure project root is on the path when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="PascoAsk query CLI")
    parser.add_argument("question", help="Natural language question")
    parser.add_argument("--source", choices=["bcc_minutes", "ldc", "civicclerk", "gis"])
    parser.add_argument("--doc-type", choices=["meeting_minutes", "ordinance", "zoning_code", "staff_report"])
    parser.add_argument("--date-from", help="ISO date filter (e.g. 2023-01-01)")
    parser.add_argument("--date-to", help="ISO date filter (e.g. 2024-12-31)")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--simple", action="store_true", help="Dense-only search (no hybrid)")
    parser.add_argument("--no-expand", action="store_true", help="Skip query expansion")
    args = parser.parse_args()

    console.print(f"\n[bold blue]Question:[/bold blue] {args.question}\n")

    if args.simple:
        _run_simple(args)
    else:
        _run_full(args)


def _run_simple(args) -> None:
    """Dense-only search — no Claude calls, fast."""
    from storage.vector_store import dense_search
    console.rule("[yellow]Dense-only search")
    results = dense_search(
        query=args.question,
        top_k=args.top_k,
        source_filter=args.source,
        doc_type_filter=args.doc_type,
        date_from=args.date_from,
        date_to=args.date_to,
    )
    _print_results(results)


def _run_full(args) -> None:
    """Full RAG pipeline: expand → hybrid → rerank → answer."""
    from retrieval.pipeline import query as rag_query
    console.rule("[green]Full RAG pipeline")
    result = rag_query(
        question=args.question,
        doc_type=args.doc_type,
        source=args.source,
        date_from=args.date_from,
        date_to=args.date_to,
        top_k=args.top_k,
        expand=not args.no_expand,
    )

    if result.expanded_queries and len(result.expanded_queries) > 1:
        console.print(f"[dim]Expanded queries: {result.expanded_queries[1:]}")

    console.print(Panel(result.answer, title="[bold green]Answer", expand=False))

    table = Table(title="Sources", show_lines=True)
    table.add_column("#", width=3)
    table.add_column("Source", width=12)
    table.add_column("Type", width=14)
    table.add_column("Date", width=12)
    table.add_column("Score", width=8, justify="right")
    table.add_column("URL")

    for i, s in enumerate(result.sources, 1):
        table.add_row(
            str(i),
            s.source,
            s.doc_type,
            s.date,
            f"{s.score:.3f}",
            s.url[:60],
        )
    console.print(table)


def _print_results(results: list[dict]) -> None:
    table = Table(title="Dense Search Results", show_lines=True)
    table.add_column("#", width=3)
    table.add_column("Source", width=12)
    table.add_column("Score", width=8, justify="right")
    table.add_column("Date", width=12)
    table.add_column("Summary / Text")
    table.add_column("URL")

    for i, r in enumerate(results, 1):
        preview = (r.get("summary") or r.get("text", ""))[:120]
        table.add_row(
            str(i),
            r.get("source", ""),
            f"{r.get('_score', 0):.3f}",
            r.get("date", ""),
            preview,
            (r.get("url", ""))[:50],
        )
    console.print(table)


if __name__ == "__main__":
    main()
