"""Custom evaluation cases: variants of real requests, each changing one thing on purpose.

Each case states its expectation in advance, derived by reasoning about the change (not from the
agent's output): either the exact gold answer of the case it varies, or targeted assertions.
Request texts are reused verbatim from the dataset so intent verdicts come from the cache.

    python code/evaluation/custom_cases.py
"""

import json
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.agent import AuditAgent  # noqa: E402
from audit.data import load_dataset, load_requests, read_csv  # noqa: E402
from audit.schema import serialize_steps  # noqa: E402

ACCESS_REQUEST = ("Could you send me a copy of the information held on my account? "
                  "I still use it, so please don't delete or change anything.")


@dataclass
class CustomCase:
    name: str
    purpose: str
    base: str  # sample or evaluation case the variant starts from
    changes: dict  # request fields overridden
    same_as_gold_of: str | None = None  # expect exactly this sample's gold status and steps
    same_as_base: bool = False  # invariance: expect exactly the agent's answer for the unchanged base request
    status: str | None = None
    must_include: list[tuple[str, str, str]] = field(default_factory=list)  # (operation, resource_id, evidence_id)
    must_exclude: list[str] = field(default_factory=list)  # resource ids that must not appear


CASES = [
    CustomCase(
        "historical_email",
        "Request sent from the subject's previous email (changed 2026-05-18, audit_282) must resolve to "
        "the same person and yield the same audit, including the record keyed only by the old email hash.",
        base="case_12", changes={"email": "minjun.kim90@gmail.com"}, same_as_gold_of="case_12",
    ),
    CustomCase(
        "email_case_insensitive",
        "An upper-cased request email is the same identifier; the audit must not change at all.",
        base="case_26", changes={"email": "ALMEIDA.MARIANA@GMAIL.COM"}, same_as_base=True,
    ),
    CustomCase(
        "access_request_not_deletion",
        "A subject with remaining personal data who asks only for a copy of it has not authorized "
        "deletion: NOT_APPLICABLE with no steps, even though findings exist.",
        base="case_24", changes={"request_text": ACCESS_REQUEST}, status="NOT_APPLICABLE",
    ),
    CustomCase(
        "later_audit_sees_resurrection",
        "case_27 is COMPLETE at 2026-09-15, but a snapshot exported 2026-09-17 shows account_3866 "
        "ACTIVE again. Auditing the same request at 2026-09-18 must report it as remaining.",
        base="case_27", changes={"audit_at": "2026-09-18T12:00:00Z"}, status="INCOMPLETE",
        must_include=[("REPORT_REMAINING_RECORD", "account_3866", "evidence_02")],
    ),
    CustomCase(
        "earlier_audit_cannot_cite_later_evidence",
        "case_30's findings come from an export taken 2026-09-15 11:44. Auditing at 09:00 that day, "
        "that export did not exist yet, so nothing may cite it.",
        base="case_30", changes={"audit_at": "2026-09-15T09:00:00Z"},
        must_exclude=[],  # checked through the global no-future-evidence assertion below
    ),
    CustomCase(
        "decoy_lookalike_excluded",
        "g.silva76@gmail.com's search hit is search_428; search_1874 belongs to the look-alike "
        "g.silva76.jr@gmail.com shown on the same screen and must never be attributed.",
        base="case_35", changes={}, must_include=[("REPORT_REMAINING_RECORD", "search_428", "evidence_09")],
        must_exclude=["search_1874"],
    ),
]


def run(agent: AuditAgent | None = None) -> list[tuple[CustomCase, bool, list[str]]]:
    agent = agent or AuditAgent(load_dataset())
    requests = {r.case_id: r for r in load_requests("sample_deletion_requests.csv") + load_requests()}
    gold = {r["case_id"]: r for r in read_csv("sample_output.csv")}
    captured = {ev.file_id: ev.captured_at for ev in agent.dataset.evidence}
    results = []
    for case in CASES:
        request = replace(requests[case.base], case_id=f"custom_{case.name}", **case.changes)
        status, steps = agent.audit(request)
        produced = json.loads(serialize_steps(steps))
        problems = []
        if case.same_as_gold_of:
            g = gold[case.same_as_gold_of]
            if status != g["deletion_status"]:
                problems.append(f"status {status} != gold {g['deletion_status']}")
            if produced != json.loads(g["steps_json"]):
                problems.append("steps differ from gold")
        if case.same_as_base:
            base_status, base_steps = agent.audit(requests[case.base])
            if (status, produced) != (base_status, json.loads(serialize_steps(base_steps))):
                problems.append("result differs from the unchanged base request")
        if case.status and status != case.status:
            problems.append(f"status {status} != expected {case.status}")
        if case.status == "NOT_APPLICABLE" and produced:
            problems.append("NOT_APPLICABLE must have no steps")
        keys = {(s["operation"], s["resource_id"], s["evidence_id"]) for s in produced}
        problems += [f"missing {k}" for k in case.must_include if k not in keys]
        problems += [f"excluded {r} appears" for r in case.must_exclude if any(s["resource_id"] == r for s in produced)]
        # Global assertion for every case: nothing cited was captured after the audit cutoff.
        late = [s["evidence_id"] for s in produced if s["evidence_id"] in captured and captured[s["evidence_id"]] > request.audit_at]
        problems += [f"cites {e} captured after audit_at" for e in late]
        results.append((case, not problems, problems))
    return results


def main() -> int:
    results = run()
    for case, ok, problems in results:
        print(f"{'PASS' if ok else 'FAIL'}  {case.name}")
        for p in problems:
            print(f"        {p}")
    print(f"\n{sum(ok for _, ok, _ in results)}/{len(results)} custom cases pass")
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
