"""Regression and adversarial tests covering 15 required scenarios.

Each test states its expectation before running. Tests marked SYNTHETIC build their own inputs
(fabricated observations or a temporary corrupt file) because the public data has no such case;
every other test varies a real public request. No hidden answers are used: "gold" means the public
dataset/sample_output.csv.

    python code/evaluation/regression_tests.py
"""

import json
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.agent import AuditAgent  # noqa: E402
from audit.data import EvidenceFile, load_dataset, load_requests, read_csv  # noqa: E402
from audit.evidence import Observation, load_evidence  # noqa: E402
from audit.policy import _retention_lapsed, state_at  # noqa: E402
from audit.schema import serialize_steps  # noqa: E402

ACCESS = ("Could you send me a copy of the information held on my account? "
          "I still use it, so please don't delete or change anything.")
MARKETING = "Please remove me from marketing lists only. I still want receipts and service messages for this account."
RETRY = ("The privacy page still says my request failed. Please inspect the actual systems and tell me whether "
         "the account and identifiable analytics data have already been removed.")


@dataclass
class Test:
    number: int
    name: str
    expectation: str
    synthetic: bool = False


class Suite:
    def __init__(self):
        self.agent = AuditAgent(load_dataset())
        self.requests = {r.case_id: r for r in load_requests("sample_deletion_requests.csv") + load_requests()}
        self.gold = {r["case_id"]: r for r in read_csv("sample_output.csv")}
        self.results: list[tuple[Test, bool, str]] = []

    def run_case(self, base: str, **changes) -> tuple[str, list[dict]]:
        request = replace(self.requests[base], case_id=f"test_{base}", **changes) if changes else self.requests[base]
        status, steps = self.agent.audit(request)
        return status, json.loads(serialize_steps(steps))

    def gold_of(self, case_id: str) -> tuple[str, list[dict]]:
        g = self.gold[case_id]
        return g["deletion_status"], json.loads(g["steps_json"])

    def check(self, test: Test, fn) -> None:
        try:
            ok, detail = fn()
        except Exception as e:  # a crashing test is a failing test
            ok, detail = False, f"{type(e).__name__}: {e}"
        self.results.append((test, ok, detail))


def has(steps: list[dict], operation: str, resource: str, evidence: str | None = None) -> bool:
    return any(s["operation"] == operation and s["resource_id"] == resource
               and (evidence is None or s["evidence_id"] == evidence) for s in steps)


def main() -> int:
    s = Suite()
    T = Test

    s.check(T(1, "historical_email_and_hash", "case_12 sent from the previous email equals case_12's gold, "
              "including the analytics record keyed only by the old email's hash"),
            lambda: (s.run_case("case_12", email="minjun.kim90@gmail.com") == s.gold_of("case_12"), "compared with gold"))

    s.check(T(2, "uppercase_identifier", "upper-casing case_26's email changes nothing"),
            lambda: (s.run_case("case_26", email="ALMEIDA.MARIANA@GMAIL.COM") == s.run_case("case_26"), "compared with base"))

    def decoy():
        status, steps = s.run_case("case_35")
        return has(steps, "REPORT_REMAINING_RECORD", "search_428", "evidence_09") and not has(steps, "REPORT_REMAINING_RECORD", "search_1874") \
            and all(st["resource_id"] != "search_1874" for st in steps), f"{status}, {len(steps)} steps"
    s.check(T(3, "decoy_lookalike", "case_35 reports search_428 and never the look-alike's search_1874"), decoy)

    def after_audit():
        status, steps = s.run_case("case_30", audit_at="2026-09-15T09:00:00Z")
        return not any(st["evidence_id"] == "evidence_92" for st in steps), f"{status}, cites {sorted({st['evidence_id'] for st in steps} - {''})}"
    s.check(T(4, "evidence_after_audit", "case_30 audited at 09:00 cannot cite evidence_92 (captured 11:44)"), after_audit)

    def reappeared():
        at_audit = s.run_case("case_27")
        later = s.run_case("case_27", audit_at="2026-09-18T12:00:00Z")
        ok = at_audit[0] == "COMPLETE" and has(at_audit[1], "VERIFY_RECORD_ABSENT", "account_3866", "evidence_92") \
            and later[0] == "INCOMPLETE" and has(later[1], "REPORT_REMAINING_RECORD", "account_3866", "evidence_02")
        return ok, f"at audit {at_audit[0]}, at 09-18 {later[0]}"
    s.check(T(5, "absent_at_audit_reappeared_later", "case_27 is COMPLETE with account_3866 absent at 09-15 "
              "(the 09-17 resurrection is invisible), and INCOMPLETE with it remaining when audited at 09-18"), reappeared)

    def replica():
        status, steps = s.run_case("case_05")
        return status == "INCOMPLETE" and has(steps, "REPORT_REMAINING_RECORD", "account_005", "evidence_54"), status
    s.check(T(6, "replica_survives_primary_delete", "case_05: the failover replica still holds account_005 -> "
              "INCOMPLETE, REPORT_REMAINING_RECORD citing evidence_54"), replica)

    def retention():
        status, steps = s.run_case("case_32")
        ok = has(steps, "REPORT_EXPOSED_PII", "tombstone_9178", "evidence_51") and has(steps, "REDACT_RECORD", "tombstone_9178") \
            and has(steps, "VERIFY_RECORD_PRESENT", "audit_record_7068") and not has(steps, "DELETE_RECORD", "tombstone_9178") \
            and not has(steps, "DELETE_RECORD", "audit_record_7068")
        return ok, status
    s.check(T(7, "retention_with_identifiers_removed", "case_32: the tombstone keeps user_542 but its name/email/phone/hash "
              "must be redacted (not deleted); the security audit record is retained and verified present"), retention)

    def retention_lapsed_synthetic():
        obs = [Observation("synthetic", "2026-01-01T00:00:00Z", "security_service", "audit_record_x", True,
                           user_ref="user_x", attrs={"retention_until": "2026-06-30T00:00:00Z"})]
        lapsed = _retention_lapsed(obs, "2026-09-01T00:00:00Z", lambda o: o.observed_at)
        kept = _retention_lapsed(obs, "2026-03-01T00:00:00Z", lambda o: o.observed_at)
        return lapsed and not kept, f"lapsed after expiry={lapsed}, lapsed before expiry={kept}"
    s.check(T(7, "retention_period_expiry", "SYNTHETIC: a security record whose retention ended before the audit is no "
              "longer retained (rule_12 only retains within the period); before expiry it is retained", synthetic=True),
            retention_lapsed_synthetic)

    def analytics():
        status, steps = s.run_case("case_22")
        return has(steps, "REPORT_EXPOSED_PII", "analytics_4440", "evidence_05") and has(steps, "ANONYMIZE_RECORD", "analytics_4440") \
            and has(steps, "VERIFY_RECORD_ANONYMIZED", "analytics_4440") and not has(steps, "DELETE_RECORD", "analytics_4440"), status
    s.check(T(8, "analytics_anonymization", "case_22: identifiable analytics_4440 is anonymized (not deleted) and verified"), analytics)

    def sync_flow():
        status, steps = s.run_case("case_15")
        ops = [st["operation"] for st in steps if st["phase"] == "REMEDIATION"]
        first_delete = min((i for i, o in enumerate(ops) if o in ("DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD")), default=len(ops))
        ok = has(steps, "REPORT_ACTIVE_DATA_FLOW", "data_flow_9846", "evidence_01") and ops and ops[0] == "DISABLE_DATA_FLOW" \
            and ops.index("DISABLE_DATA_FLOW") < first_delete
        return ok, f"{status}, remediation order {ops}"
    s.check(T(9, "active_sync_flow", "case_15: the enabled connector is reported ACTIVE and disabled before any record change"), sync_flow)

    def unsafe_backup():
        status, steps = s.run_case("case_17")
        ok = has(steps, "REPORT_RESTORE_RISK", "backup_3245", "evidence_01") and has(steps, "BLOCK_RESTORE", "backup_3245") \
            and has(steps, "VERIFY_RESTORE_BLOCKED", "backup_3245") and not has(steps, "DELETE_RECORD", "backup_3245")
        return ok, status
    s.check(T(10, "unsafe_backup_restore", "case_17: a restorable backup is a restore risk, blocked and retained (never deleted)"), unsafe_backup)

    def empty_snapshot():
        status, steps = s.run_case("case_01")
        return status == "COMPLETE" and has(steps, "VERIFY_RECORD_ABSENT", "account_001", "evidence_54"), status
    s.check(T(11, "full_snapshot_omission_proves_deletion", "case_01: account_001 is missing from both full account tables "
              "in evidence_54 -> verified absent citing it"), empty_snapshot)

    def omission_limits():
        status, steps = s.run_case("case_26")
        return status == "COMPLETE" and has(steps, "VERIFY_RECORD_PRESENT", "audit_record_5416", "evidence_92"), status
    s.check(T(11, "filtered_listing_cannot_remove_retained_record", "case_26: evidence_51's security listing omits "
              "audit_record_5416, but omission never proves a retained record is gone -> still verified present"), omission_limits)

    def tie():
        status, steps = s.run_case("case_28")
        ok = not any(st["system_id"] == "account_service" for st in steps) and not any(st["system_id"] == "analytics_platform" for st in steps)
        return ok, f"{status}; systems {sorted({st['system_id'] for st in steps})}"
    s.check(T(12, "ambiguous_batch_not_guessed", "case_28: three NOT_STARTED subjects share two FAILED account deletes and "
              "two anonymized analytics records with no identifiers -> no account or analytics step is guessed"), tie)

    def pigeonhole():
        status, steps = s.run_case("case_18")
        return has(steps, "VERIFY_RECORD_ABSENT", "account_5521"), status
    s.check(T(12, "batch_resolved_by_matching", "case_18: two other cases must use both of {account_5193, account_5823}, "
              "so account_5521 is case_18's -> verified absent"), pigeonhole)

    s.check(T(13, "access_request", "case_24 with an access-only text -> NOT_APPLICABLE, [] although data remains"),
            lambda: (s.run_case("case_24", request_text=ACCESS) == ("NOT_APPLICABLE", []), "checked"))
    s.check(T(13, "marketing_opt_out", "case_24 with 'remove me from marketing lists only' -> NOT_APPLICABLE, []"),
            lambda: (s.run_case("case_24", request_text=MARKETING) == ("NOT_APPLICABLE", []), "checked"))

    def retry():
        status, steps = s.run_case("case_26", request_text=RETRY)
        return status == "COMPLETE" and steps == s.run_case("case_26")[1], status
    s.check(T(14, "retry_failed_deletion", "case_26 with 'my request failed, tell me whether data was removed' is still a "
              "deletion audit: same COMPLETE result as the original text"), retry)

    def malformed():
        with tempfile.TemporaryDirectory() as tmp:
            bad_json = Path(tmp) / "records" / "customer_data_snapshot_bundle_29990101.json"
            bad_json.parent.mkdir(parents=True)
            bad_json.write_text('{"exported_at": "2026-09-01T00:00:00Z", "snapshots": [ {"captured_at": ', encoding="utf-8")
            bad_log = Path(tmp) / "logs" / "service_events_test_29990101.log"
            bad_log.parent.mkdir(parents=True)
            bad_log.write_text("garbage line without structure\n2026-09-01T00:00:00Z level=INFO service=search_service "
                               "trace_id=t1 event=index_query_completed resource_id=search_1 result=hit\n\x00\x01\n", encoding="utf-8")
            files = [EvidenceFile("synthetic_json", bad_json, "test", "2026-09-01T00:00:00Z"),
                     EvidenceFile("synthetic_log", bad_log, "test", "2026-09-01T00:00:00Z")]
            # parser_for matches on the path below an "evidence" folder; emulate that layout.
            for f in files:
                target = Path(tmp) / "evidence" / f.path.relative_to(tmp)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(f.path.read_bytes())
            files = [replace(f, path=Path(tmp) / "evidence" / f.path.relative_to(tmp)) for f in files]
            ev = load_evidence(files)
        ok = len(ev.errors) == 1 and "synthetic_json" in ev.errors[0] and [o.resource_id for o in ev.observations] == ["search_1"]
        return ok, f"errors={len(ev.errors)}, observations={[o.resource_id for o in ev.observations]}"
    s.check(T(15, "malformed_evidence", "SYNTHETIC: a truncated JSON bundle is reported and skipped without stopping the "
              "run; a log with garbage and binary lines still yields its one valid event", synthetic=True), malformed)

    def contradictory():
        seen = Observation("syn_a", "2026-09-10T10:00:00Z", "account_service", "account_x", True, attrs={"role": "read_replica"})
        gone = Observation("syn_b", "2026-09-10T11:00:00Z", "account_service", "account_x", False)
        state = state_at(("account_service", "account_x"), [seen, gone], [], "2026-09-15T00:00:00Z", lambda o: o.observed_at)
        return state.exists and state.evidence_id == "syn_a", f"exists={state.exists}, cited={state.evidence_id}"
    s.check(T(15, "contradictory_sources", "SYNTHETIC: the service says 404 but a read replica still holds the record -> "
              "it still exists (any surviving copy), citing the replica evidence", synthetic=True), contradictory)

    for test, ok, detail in s.results:
        tag = " [synthetic]" if test.synthetic else ""
        print(f"{'PASS' if ok else 'FAIL'}  #{test.number:<2} {test.name}{tag}  ({detail})")
    passed = sum(ok for _, ok, _ in s.results)
    print(f"\n{passed}/{len(s.results)} regression tests pass, covering scenarios "
          f"{sorted({t.number for t, _, _ in s.results})}")
    return 0 if passed == len(s.results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
