"""Entry point: audit every request in a requests CSV and write the predictions CSV.

    python code/main.py                                   # dataset/deletion_requests.csv -> output.csv
    python code/main.py --requests sample_deletion_requests.csv --out sample_predictions.csv
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from audit.agent import AuditAgent  # noqa: E402
from audit.data import case_sort_key, load_dataset, load_requests  # noqa: E402
from audit.llm import LEDGER  # noqa: E402
from audit.paths import OUTPUT_PATH, REPO_ROOT  # noqa: E402
from audit.schema import serialize_steps, validate_row  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requests", default="deletion_requests.csv", help="file name inside dataset/")
    parser.add_argument("--out", default=str(OUTPUT_PATH), help="output CSV path (relative to repo root)")
    args = parser.parse_args()

    agent = AuditAgent(load_dataset())
    requests = sorted(load_requests(args.requests), key=lambda r: case_sort_key(r.case_id))

    rows, invalid = [], 0
    for request in requests:
        status, steps = agent.audit(request)
        steps_json = serialize_steps(steps)
        if errors := validate_row(status, steps_json):
            invalid += 1
            print(f"[invalid] {request.case_id}: {'; '.join(errors)}", file=sys.stderr)
        rows.append((request.case_id, status, steps_json))

    out_path = REPO_ROOT / args.out
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["case_id", "deletion_status", "steps_json"])
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {out_path} ({invalid} invalid)")

    # Model-call ledger for this run (live and cached calls), consumed by evaluation/usage_report.py.
    # The default run records it beside the reports; any other --out keeps its ledger beside that file.
    ledger = {"requests_file": args.requests, "requests": len(rows), "calls": LEDGER.summary()}
    if out_path == OUTPUT_PATH or args.out == "sample_predictions.csv":
        ledger_path = Path(__file__).resolve().parent / "evaluation" / f"usage_ledger_{Path(args.requests).stem}.json"
    else:
        ledger_path = out_path.with_suffix(".ledger.json")
    ledger_path.write_text(json.dumps(ledger, indent=1), encoding="utf-8")
    live = [c for c in LEDGER.calls if not c.cached]
    print(f"model calls: {len(LEDGER.calls)} ({len(live)} live, {len(LEDGER.calls) - len(live)} from cache)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
