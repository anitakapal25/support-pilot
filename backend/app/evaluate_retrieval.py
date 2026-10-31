"""Deterministic retrieval calibration/report. Held-out cases never choose thresholds."""

import argparse
import json
from pathlib import Path

from app.config import Settings
from app.database import make_engine
from app.retrieval import Retriever


def evaluate(engine, settings, dataset: Path):
    cases = json.loads(dataset.read_text())
    retriever = Retriever(engine, settings)
    results = [(case, retriever.search(case["query"])) for case in cases]

    def counts(split, threshold):
        selected = [(case, rows) for case, rows in results if case["split"] == split]
        positive = sum(case["answerable"] for case, rows in selected)
        recalled = sum(
            bool(set(case["expected_articles"]) & {row["article_id"] for row in rows})
            for case, rows in selected
            if case["answerable"]
        )
        tp = fp = tn = fn = 0
        for case, rows in selected:
            accepted = any(row["coverage"] >= threshold and row["matched"] >= 2 for row in rows)
            if accepted and case["answerable"]:
                tp += 1
            elif accepted:
                fp += 1
            elif case["answerable"]:
                fn += 1
            else:
                tn += 1
        return {
            "cases": len(selected),
            "answerable": positive,
            "recall_at_5_count": recalled,
            "recall_at_5": recalled / max(1, positive),
            "gate_true_positive": tp,
            "gate_false_positive": fp,
            "gate_true_negative": tn,
            "gate_false_negative": fn,
        }

    trials = [{"threshold": value, **counts("dev", value)} for value in (0.7, 0.8, 0.9, 1.0)]
    chosen = min(
        trials,
        key=lambda row: (row["gate_false_positive"], -row["gate_true_positive"], row["threshold"]),
    )["threshold"]
    report = {
        "dataset_status": "Regression suite after an initial held-out false positive; fresh independent validation remains required.",
        "embedding": retriever.embedder.identity,
        "calibrated_threshold": chosen,
        "configured_threshold": settings.evidence_coverage,
        "calibration_trials": trials,
        "dev": counts("dev", chosen),
        "heldout": counts("heldout", chosen),
        "cases": [
            {
                "id": case["id"],
                "split": case["split"],
                "expected_articles": case["expected_articles"],
                "retrieved_articles": [row["article_id"] for row in rows],
                "max_coverage": max(
                    (row["coverage"] for row in rows if row["matched"] >= 2), default=0
                ),
            }
            for case, rows in results
        ],
    }
    report["passed"] = (
        all(
            report[split]["recall_at_5"] >= 0.9 and report[split]["gate_false_positive"] == 0
            for split in ("dev", "heldout")
        )
        and settings.evidence_coverage == chosen
    )
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("evals/reports/retrieval.json"))
    args = parser.parse_args()
    engine = make_engine(Settings())
    try:
        report = evaluate(engine, Settings(), Path("evals/retrieval.json"))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {
                    key: report[key]
                    for key in (
                        "embedding",
                        "calibrated_threshold",
                        "configured_threshold",
                        "dev",
                        "heldout",
                        "passed",
                    )
                },
                indent=2,
            )
        )
        raise SystemExit(0 if report["passed"] else 1)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
