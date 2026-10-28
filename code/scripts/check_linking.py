"""Measure identity resolution and resource linking against the 32 gold sample cases.

recall    gold resources (any step) that the linker connects to the case's user by audit_at
extras    linked resources the gold answer never mentions, grouped by system (candidates for
          decoys, irrelevant records, or legitimately compliant records the gold omits)

    python code/scripts/check_linking.py [--verbose]
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import load_dataset, load_requests, read_csv  # noqa: E402
from audit.evidence import load_evidence  # noqa: E402
from audit.identity import IdentityResolver  # noqa: E402
from audit.linking import ResourceIndex, WorkflowAttributor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    dataset = load_dataset()
    resolver = IdentityResolver(dataset)
    captured = {ev.file_id: ev.captured_at for ev in dataset.evidence}
    index = ResourceIndex(load_evidence(dataset.evidence), dataset.activity, captured)
    samples = load_requests("sample_deletion_requests.csv")
    attributor = WorkflowAttributor(samples + load_requests(), resolver, index, dataset.activity)
    gold = {r["case_id"]: json.loads(r["steps_json"]) for r in read_csv("sample_output.csv")}

    hit = total = ties = 0
    extras: Counter = Counter()
    for req in samples:
        identity = resolver.resolve(req.email, req.audit_at)
        linked = dict(index.linked(identity, req.audit_at))
        attribution = attributor.attribute(req)
        for key in attribution.resources:
            linked.setdefault(key, [])
        ties += bool(attribution.ambiguous)
        wanted = {(s["system_id"], s["resource_id"]) for s in gold[req.case_id]}
        missing = sorted(wanted - linked.keys())
        extra = sorted(linked.keys() - wanted)
        hit, total = hit + len(wanted & linked.keys()), total + len(wanted)
        extras.update(system for system, _ in extra)
        if missing or args.verbose:
            print(f"{req.case_id} {identity.user_id} emails={len(identity.emails)}: "
                  f"{len(wanted & linked.keys())}/{len(wanted)} linked"
                  + (f"  MISSING {missing}" if missing else "")
                  + (f"  extra {extra}" if args.verbose and extra else ""))

    print(f"\nlink recall: {hit}/{total}   cases with unresolved workflow ties: {ties}")
    print("extra linked resources by system:", dict(extras.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
