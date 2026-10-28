"""Stage 5: turn per-resource decisions into the deletion status and ordered steps.

INCOMPLETE: every violation as a cited finding, its remediation, then a verification of the
required final state for every relevant record (future checks, so no evidence).
COMPLETE: one verification per relevant record, citing the evidence that proves its state now.
Ordering (phase, operation table order, system_id, resource_id) is applied by schema.order_steps.
"""

from .policy import Decision
from .schema import COMPLETE, INCOMPLETE, Step


def build_steps(decisions: list[Decision]) -> tuple[str, list[Step]]:
    violations = [d for d in decisions if d.violation]
    steps: list[Step] = []
    if violations:
        for d in violations:
            finding, remediation, _ = d.ops()
            steps.append(Step.make(finding, *d.resource, evidence_id=d.evidence_id))
            steps.append(Step.make(remediation, *d.resource))
        for d in decisions:
            steps.append(Step.make(d.ops()[2], *d.resource))
        return INCOMPLETE, steps

    for d in decisions:
        if d.evidence_id:  # a COMPLETE verdict only lists checks the evidence actually proves
            steps.append(Step.make(d.ops()[2], *d.resource, evidence_id=d.evidence_id))
    return COMPLETE, steps
