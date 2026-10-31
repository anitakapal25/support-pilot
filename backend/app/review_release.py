"""Validate human labels against the original review packet; calibrate advisory judges."""

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.evaluate_release import count, digest


def validate_review(original, reviewed):
    if any(reviewed.get(key) != original[key] for key in ("run_id", "dataset_sha256")):
        raise ValueError("Review belongs to a different evaluation run")
    if (
        reviewed.get("attested_human_review") is not True
        or not reviewed.get("reviewer")
        or not reviewed.get("reviewed_at")
    ):
        raise ValueError("Named, dated human review attestation is required")
    if not isinstance(reviewed["reviewer"], str) or not reviewed["reviewer"].strip():
        raise ValueError("Reviewer name is required")
    datetime.fromisoformat(reviewed["reviewed_at"])
    labels = reviewed.get("cases", [])
    if len(labels) != len(original["cases"]) or len({row["id"] for row in labels}) != len(labels):
        raise ValueError("Review must cover every scenario exactly once")
    expected = {row["id"]: row for row in original["cases"]}
    for row in labels:
        source = expected.get(row["id"])
        evidence = {
            key: row[key]
            for key in ("id", "expected", "inputs", "messages", "sources", "order_evidence")
        }
        if (
            not source
            or digest(evidence) != source["evidence_sha256"]
            or row.get("evidence_sha256") != source["evidence_sha256"]
        ):
            raise ValueError("Review evidence changed")
        if any(type(row.get(key)) is not bool for key in ("grounded", "resolved")):
            raise ValueError("Every scenario needs boolean grounded/resolved labels")
    result = {key: count(row[key] for row in labels) for key in ("grounded", "resolved")}
    result["passed"] = all(result[key]["rate"] >= 0.9 for key in ("grounded", "resolved"))
    return result


def calibrate(human, judge):
    """Agreement is descriptive. A judge never replaces human release signoff."""
    if any(human[key] != judge.get(key) for key in ("run_id", "dataset_sha256")):
        raise ValueError("Judge labels must use the same run")
    predicted = {row["id"]: row for row in judge["cases"]}
    if len(predicted) != len(judge["cases"]) or set(predicted) != {
        row["id"] for row in human["cases"]
    }:
        raise ValueError("Judge must label the same unique scenarios")
    result = {"advisory_only": True}
    for field in ("grounded", "resolved"):
        pairs = [(row[field], predicted[row["id"]].get(field)) for row in human["cases"]]
        if any(type(a) is not bool or type(b) is not bool for a, b in pairs):
            raise ValueError("Calibration requires boolean labels")
        tp = sum(a and b for a, b in pairs)
        tn = sum(not a and not b for a, b in pairs)
        fp = sum(not a and b for a, b in pairs)
        fn = sum(a and not b for a, b in pairs)
        n = len(pairs)
        accuracy = (tp + tn) / n if n else 0
        chance = ((tp + fn) * (tp + fp) + (tn + fp) * (tn + fn)) / (n * n) if n else 1
        kappa = (accuracy - chance) / (1 - chance) if chance < 1 else None
        result[field] = {
            "tp": tp,
            "tn": tn,
            "fp": fp,
            "fn": fn,
            "agreement": accuracy,
            "kappa": kappa,
            "calibrated": n >= 20
            and min(tp + fn, tn + fp) >= 5
            and accuracy >= 0.9
            and kappa is not None
            and kappa >= 0.8,
        }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--reviewed", type=Path, required=True)
    parser.add_argument("--judge", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    original = json.loads(args.original.read_text())
    reviewed = json.loads(args.reviewed.read_text())
    result = validate_review(original, reviewed)
    if args.judge:
        result["judge"] = calibrate(reviewed, json.loads(args.judge.read_text()))
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
