"""Cross-check evidence parsers against the evidence citations in dataset/sample_output.csv.

Every gold step that cites a file claims "this file shows resource X in state Y". For each such
step whose file has a parser, find the parsed observation of X in that file (latest at or before
the case's audit_at) and confirm it supports the claimed state.

    python code/scripts/check_parsers.py [--verbose]
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import load_evidence_manifest, load_requests, read_csv  # noqa: E402
from audit.evidence import Coverage, Observation, load_evidence, parser_for  # noqa: E402

# Gold operation -> predicate an observation must satisfy to support it.
SUPPORTS = {
    "REPORT_REMAINING_RECORD": lambda o: o.exists,
    "REPORT_EXPOSED_PII": lambda o: o.exists and bool(o.direct_identifiers),
    "REPORT_ACTIVE_DATA_FLOW": lambda o: o.attrs.get("enabled") is True,
    "REPORT_RESTORE_RISK": lambda o: o.attrs.get("restore_allowed") is True,
    "VERIFY_RECORD_ABSENT": lambda o: not o.exists,
    "VERIFY_RECORD_PRESENT": lambda o: o.exists,
    "VERIFY_RECORD_REDACTED": lambda o: o.exists and not o.direct_identifiers,
    "VERIFY_RECORD_ANONYMIZED": lambda o: o.exists and not o.direct_identifiers and not o.user_ref,
    "VERIFY_DATA_FLOW_DISABLED": lambda o: o.attrs.get("enabled") is False,
    "VERIFY_RESTORE_BLOCKED": lambda o: o.attrs.get("restore_allowed") is False,
}


def describe(o: Observation) -> str:
    state = {k: o.attrs[k] for k in ("enabled", "restore_allowed", "status_code") if k in o.attrs}
    return f"exists={o.exists} ref={o.user_ref} ids={sorted(o.direct_identifiers)} {state} @{o.observed_at}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true", help="print passing checks too")
    args = parser.parse_args()

    manifest = {ev.file_id: ev for ev in load_evidence_manifest()}
    evidence = load_evidence(list(manifest.values()))
    by_file_resource: dict[tuple[str, str], list[Observation]] = defaultdict(list)
    for o in evidence.observations:
        by_file_resource[(o.file_id, o.resource_id)].append(o)
    coverage_by_file: dict[str, list[Coverage]] = defaultdict(list)
    for c in evidence.coverage:
        coverage_by_file[c.file_id].append(c)

    audit_at = {r.case_id: r.audit_at for r in load_requests("sample_deletion_requests.csv")}
    results: Counter = Counter()
    for row in read_csv("sample_output.csv"):
        for step in json.loads(row["steps_json"]):
            file_id = step["evidence_id"]
            if not file_id.startswith("evidence_"):
                continue
            if parser_for(manifest[file_id]) is None:
                results["no parser"] += 1
                continue
            seen = [o for o in by_file_resource[(file_id, step["resource_id"])] if o.observed_at <= audit_at[row["case_id"]]]
            label = f"{row['case_id']} {step['operation']:<26} {step['resource_id']:<18} {file_id}"
            if not seen and step["operation"] == "VERIFY_RECORD_ABSENT":
                omitted = [
                    c for c in coverage_by_file[file_id]
                    if c.system_id == step["system_id"] and c.observed_at <= audit_at[row["case_id"]]
                    and step["resource_id"] not in c.resource_ids
                ]
                if omitted:
                    results["pass"] += 1
                    if args.verbose:
                        print(f"{'pass':<10} {label}  omitted from {', '.join(sorted({c.table for c in omitted}))}")
                    continue
            if not seen:
                results["not found"] += 1
                print(f"NOT FOUND  {label}")
                continue
            latest = max(seen, key=lambda o: o.observed_at)
            ok = SUPPORTS[step["operation"]](latest)
            results["pass" if ok else "FAIL"] += 1
            if not ok or args.verbose:
                print(f"{'pass' if ok else 'FAIL':<10} {label}  {describe(latest)}")

    print("\n" + ", ".join(f"{k}: {v}" for k, v in sorted(results.items())))
    return 1 if results["FAIL"] or results["not found"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
