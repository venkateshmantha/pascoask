"""
RAGAS evaluation harness for PascoAsk.

Measures: faithfulness, answer_relevancy, context_precision

Usage:
    python eval/run_eval.py
    python eval/run_eval.py --qa-file eval/golden_qa.json --max 10
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.table import Table

console = Console()
logger = logging.getLogger(__name__)

RESULTS_DIR = Path(__file__).parent / "results"


def run_eval(qa_path: Path, max_pairs: int = 0) -> dict:
    qa_pairs = json.loads(qa_path.read_text())
    # Filter out placeholder entries
    qa_pairs = [q for q in qa_pairs if not q.get("expected_answer", "").startswith("TODO")]
    if not qa_pairs:
        console.print("[red]No valid QA pairs found. Fill in eval/golden_qa.json first.")
        return {}

    if max_pairs:
        qa_pairs = qa_pairs[:max_pairs]

    console.print(f"Running eval on {len(qa_pairs)} QA pairs...")

    from retrieval.pipeline import query as rag_query

    questions, answers, ground_truths, contexts = [], [], [], []

    for pair in qa_pairs:
        q = pair["question"]
        console.print(f"  Q: {q[:80]}")
        try:
            result = rag_query(
                question=q,
                doc_type=pair.get("doc_type"),
                top_k=5,
            )
            questions.append(q)
            answers.append(result.answer)
            ground_truths.append(pair["expected_answer"])
            contexts.append([s.text for s in result.sources])
        except Exception as exc:
            logger.error("Query failed for '%s': %s", q, exc)

    if not questions:
        console.print("[red]All queries failed.")
        return {}

    console.print("[yellow]Computing RAGAS metrics...")
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, context_precision, faithfulness

        dataset = Dataset.from_dict({
            "question": questions,
            "answer": answers,
            "ground_truth": ground_truths,
            "contexts": contexts,
        })
        scores = evaluate(dataset, metrics=[faithfulness, answer_relevancy, context_precision])
        results_dict = dict(scores)
    except Exception as exc:
        console.print(f"[red]RAGAS evaluation failed: {exc}")
        console.print("[yellow]Saving raw Q&A output only.")
        results_dict = {}

    output = {
        "timestamp": datetime.utcnow().isoformat(),
        "num_pairs": len(questions),
        "ragas_scores": results_dict,
        "qa_results": [
            {
                "question": q,
                "answer": a,
                "ground_truth": gt,
                "num_contexts": len(ctx),
            }
            for q, a, gt, ctx in zip(questions, answers, ground_truths, contexts)
        ],
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"{ts}.json"
    out_path.write_text(json.dumps(output, indent=2))
    console.print(f"[green]Results saved to {out_path}")

    if results_dict:
        table = Table(title="RAGAS Scores")
        table.add_column("Metric")
        table.add_column("Score", justify="right")
        for metric, score in results_dict.items():
            table.add_row(metric, f"{score:.3f}" if isinstance(score, float) else str(score))
        console.print(table)

    return output


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="PascoAsk RAGAS eval")
    parser.add_argument(
        "--qa-file",
        default="eval/golden_qa.json",
        help="Path to golden QA JSON file",
    )
    parser.add_argument("--max", type=int, default=0, help="Max QA pairs to evaluate (0=all)")
    args = parser.parse_args()
    run_eval(Path(args.qa_file), max_pairs=args.max)


if __name__ == "__main__":
    main()
