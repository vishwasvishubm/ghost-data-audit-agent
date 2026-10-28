"""Score a predictions CSV against a gold CSV (default: the 32 public samples).

    python code/main.py --requests sample_deletion_requests.csv --out sample_predictions.csv
    python code/evaluation/main.py --pred sample_predictions.csv [--show-diffs]

Metrics:
  status accuracy    deletion_status matches
  step P/R/F1        steps matched as (phase, system_id, resource_id, operation, state, evidence_id) sets
  field accuracy     per-field agreement of position-aligned steps
  sequence accuracy  steps_json identical as an ordered array (the strictest measure)
"""

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELDS = ("phase", "system_id", "resource_id", "operation", "state", "evidence_id")


def load(path: Path) -> dict[str, tuple[str, list[dict]]]:
    with open(path, encoding="utf-8", newline="") as f:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(f)}


def step_key(step: dict) -> tuple:
    return tuple(step.get(k, "") for k in FIELDS)


def evaluate(gold: dict, pred: dict) -> dict:
    status_ok = seq_ok = 0
    tp = fp = fn = 0
    missing_n = extra_n = misordered = 0
    field_hits, field_total = Counter(), 0
    confusion = Counter()
    failures = []

    for case_id, (g_status, g_steps) in gold.items():
        p_status, p_steps = pred.get(case_id, ("MISSING", []))
        confusion[(g_status, p_status)] += 1
        status_ok += g_status == p_status

        g_keys, p_keys = [step_key(s) for s in g_steps], [step_key(s) for s in p_steps]
        matched = sum((Counter(g_keys) & Counter(p_keys)).values())
        tp, fp, fn = tp + matched, fp + len(p_keys) - matched, fn + len(g_keys) - matched

        for g, p in zip(g_steps, p_steps):
            for k in FIELDS:
                field_hits[k] += g.get(k) == p.get(k)
        field_total += max(len(g_steps), len(p_steps))

        exact = g_status == p_status and g_keys == p_keys
        seq_ok += exact
        missing_n += len(g_keys) - matched
        extra_n += len(p_keys) - matched
        misordered += Counter(g_keys) == Counter(p_keys) and g_keys != p_keys
        if not exact:
            failures.append((case_id, g_status, p_status, g_keys, p_keys))

    n = len(gold)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {
        "cases": n,
        "status_accuracy": status_ok / n,
        "step_precision": precision,
        "step_recall": recall,
        "step_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "field_accuracy": {k: field_hits[k] / field_total if field_total else 1.0 for k in FIELDS},
        "sequence_accuracy": seq_ok / n,
        "missing_steps": missing_n,
        "extra_steps": extra_n,
        "misordered_cases": misordered,  # same steps, wrong order
        "confusion": confusion,
        "failures": failures,
    }


def print_report(m: dict, show_diffs: bool) -> None:
    print(f"cases               {m['cases']}")
    print(f"status accuracy     {m['status_accuracy']:.1%}")
    print(f"step P / R / F1     {m['step_precision']:.1%} / {m['step_recall']:.1%} / {m['step_f1']:.1%}")
    print(f"sequence accuracy   {m['sequence_accuracy']:.1%}")
    print(f"missing / extra steps {m['missing_steps']} / {m['extra_steps']}   misordered cases {m['misordered_cases']}")
    print("field accuracy      " + "  ".join(f"{k}={v:.1%}" for k, v in m["field_accuracy"].items()))
    print("status confusion (gold -> pred):")
    for (g, p), c in sorted(m["confusion"].items()):
        print(f"  {g:>15} -> {p:<15} {c}")
    print(f"failing cases: {len(m['failures'])}")
    for case_id, g_status, p_status, g_keys, p_keys in m["failures"]:
        print(f"  {case_id}: gold={g_status} pred={p_status}")
        if show_diffs:
            for k in g_keys:
                if k not in p_keys:
                    print(f"      missing  {' '.join(k)}")
            for k in p_keys:
                if k not in g_keys:
                    print(f"      extra    {' '.join(k)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", default="dataset/sample_output.csv")
    parser.add_argument("--pred", default="sample_predictions.csv")
    parser.add_argument("--show-diffs", action="store_true")
    args = parser.parse_args()
    print_report(evaluate(load(REPO_ROOT / args.gold), load(REPO_ROOT / args.pred)), args.show_diffs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
