"""Submission checks for a predictions CSV that need no gold answers (run on all 518 rows).

    python code/evaluation/check_output.py [--pred output.csv] [--requests deletion_requests.csv]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import case_sort_key, load_requests, read_csv  # noqa: E402
from audit.schema import validate_row  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}


def check(pred_path: Path, requests_file: str) -> tuple[dict, list[str]]:
    with open(pred_path, encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        header, rows = reader.fieldnames, list(reader)
    requests = {r.case_id: r for r in load_requests(requests_file)}
    captured = {r["file_id"]: r["captured_at"] for r in read_csv("evidence_files.csv")}
    known_ids = set(captured) | {r["activity_id"] for r in read_csv("system_activity.csv")} | {
        r["audit_id"] for r in read_csv("user_audit_log.csv")}

    problems = []
    if header != ["case_id", "deletion_status", "steps_json"]:
        problems.append(f"header is {header}")
    ids = [r["case_id"] for r in rows]
    if sorted(ids) != sorted(requests) or len(ids) != len(set(ids)):
        problems.append("case ids do not match the requests one-to-one")
    if ids != sorted(ids, key=case_sort_key):
        problems.append("rows are not sorted by case_id")
    cited = late = 0
    for r in rows:
        problems += [f"{r['case_id']}: {e}" for e in validate_row(r["deletion_status"], r["steps_json"])]
        audit_at = requests[r["case_id"]].audit_at if r["case_id"] in requests else ""
        for s in json.loads(r["steps_json"]):
            if set(s) != STEP_FIELDS:
                problems.append(f"{r['case_id']}: step fields {sorted(set(s) ^ STEP_FIELDS)} differ from the schema")
            if s["evidence_id"]:
                cited += 1
                if s["evidence_id"] not in known_ids:
                    problems.append(f"{r['case_id']}: unknown evidence id {s['evidence_id']}")
                elif captured.get(s["evidence_id"], "") > audit_at:
                    late += 1
                    problems.append(f"{r['case_id']}: cites {s['evidence_id']} captured after audit_at")
    stats = {"rows": len(rows), "citations": cited, "late_citations": late}
    return stats, problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pred", default="output.csv")
    parser.add_argument("--requests", default="deletion_requests.csv")
    args = parser.parse_args()
    stats, problems = check(REPO_ROOT / args.pred, args.requests)
    for p in problems[:50]:
        print(p)
    print(f"{stats['rows']} rows, {stats['citations']} citations checked, {len(problems)} problems")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
