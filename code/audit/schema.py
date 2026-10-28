"""Output contract: allowed operations, canonical step ordering, serialization and validation."""

import json
from dataclasses import asdict, dataclass

COMPLETE = "COMPLETE"
INCOMPLETE = "INCOMPLETE"
NOT_APPLICABLE = "NOT_APPLICABLE"
STATUSES = (COMPLETE, INCOMPLETE, NOT_APPLICABLE)

PHASES = ("FINDING", "REMEDIATION", "VERIFICATION")

# operation -> (phase, state), in the order of the problem statement's table.
# Dict order is the within-phase sort order.
OPERATIONS: dict[str, tuple[str, str]] = {
    "REPORT_REMAINING_RECORD": ("FINDING", "PRESENT"),
    "REPORT_EXPOSED_PII": ("FINDING", "PRESENT"),
    "REPORT_ACTIVE_DATA_FLOW": ("FINDING", "ACTIVE"),
    "REPORT_RESTORE_RISK": ("FINDING", "ACTIVE"),
    "DISABLE_DATA_FLOW": ("REMEDIATION", "DISABLED"),
    "BLOCK_RESTORE": ("REMEDIATION", "BLOCKED"),
    "DELETE_RECORD": ("REMEDIATION", "ABSENT"),
    "REDACT_RECORD": ("REMEDIATION", "REDACTED"),
    "ANONYMIZE_RECORD": ("REMEDIATION", "ANONYMIZED"),
    "VERIFY_RECORD_ABSENT": ("VERIFICATION", "ABSENT"),
    "VERIFY_RECORD_PRESENT": ("VERIFICATION", "PRESENT"),
    "VERIFY_RECORD_REDACTED": ("VERIFICATION", "REDACTED"),
    "VERIFY_RECORD_ANONYMIZED": ("VERIFICATION", "ANONYMIZED"),
    "VERIFY_DATA_FLOW_DISABLED": ("VERIFICATION", "DISABLED"),
    "VERIFY_RESTORE_BLOCKED": ("VERIFICATION", "BLOCKED"),
}
_OP_RANK = {op: i for i, op in enumerate(OPERATIONS)}


@dataclass(frozen=True)
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str = ""

    @classmethod
    def make(cls, operation: str, system_id: str, resource_id: str, evidence_id: str = "") -> "Step":
        phase, state = OPERATIONS[operation]
        return cls(phase, system_id, resource_id, operation, state, evidence_id)

    def sort_key(self) -> tuple:
        return (PHASES.index(self.phase), _OP_RANK[self.operation], self.system_id, self.resource_id)


def order_steps(steps: list[Step]) -> list[Step]:
    """Phase, then operation order, then system_id, then resource_id; duplicates dropped."""
    return sorted(set(steps), key=Step.sort_key)


def serialize_steps(steps: list[Step]) -> str:
    rows = [{"step_no": i, **asdict(s)} for i, s in enumerate(order_steps(steps), start=1)]
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def validate_row(status: str, steps_json: str) -> list[str]:
    """Return contract violations for one output row (empty list means valid)."""
    errors: list[str] = []
    if status not in STATUSES:
        return [f"unknown status {status!r}"]
    try:
        steps = json.loads(steps_json)
    except json.JSONDecodeError as e:
        return [f"steps_json is not JSON: {e}"]
    if not isinstance(steps, list):
        return ["steps_json is not an array"]
    if status == NOT_APPLICABLE:
        return [] if steps == [] else ["NOT_APPLICABLE must have []"]
    if not steps:
        errors.append(f"{status} must have at least one step")

    for i, s in enumerate(steps, start=1):
        if s.get("step_no") != i:
            errors.append(f"step {i}: step_no is {s.get('step_no')!r}")
        op = s.get("operation")
        if op not in OPERATIONS:
            errors.append(f"step {i}: unknown operation {op!r}")
            continue
        phase, state = OPERATIONS[op]
        if (s.get("phase"), s.get("state")) != (phase, state):
            errors.append(f"step {i}: {op} must be {phase}/{state}")
        evidence = s.get("evidence_id", "")
        if status == COMPLETE:
            if phase != "VERIFICATION":
                errors.append(f"step {i}: COMPLETE allows only VERIFICATION steps")
            if not evidence:
                errors.append(f"step {i}: COMPLETE verification must cite evidence")
        elif phase == "FINDING" and not evidence:
            errors.append(f"step {i}: finding must cite evidence")
        elif phase != "FINDING" and evidence:
            errors.append(f"step {i}: proposed {phase.lower()} must have empty evidence_id")

    keys = [
        (PHASES.index(s["phase"]), _OP_RANK[s["operation"]], s["system_id"], s["resource_id"])
        for s in steps
        if s.get("operation") in OPERATIONS and s.get("phase") in PHASES
    ]
    if keys != sorted(keys):
        errors.append("steps are not in canonical order")
    return errors
