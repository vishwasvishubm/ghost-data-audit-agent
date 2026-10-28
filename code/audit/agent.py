"""Orchestrates the audit pipeline for a single deletion request."""

from dataclasses import dataclass

from .data import Dataset, Request, load_requests
from .evidence import Evidence, load_evidence
from .identity import Identity, IdentityResolver
from .intent import IntentClassifier
from .linking import Attribution, Link, ResourceIndex, ResourceKey, WorkflowAttributor
from .planner import build_steps
from .policy import plan_resource_actions
from .schema import NOT_APPLICABLE, Step


@dataclass
class CaseContext:
    """Everything known about one request's subject at its audit cutoff."""

    request: Request
    identity: Identity
    links: dict[ResourceKey, list[Link]]  # resources tied to the subject by evidence
    attribution: Attribution  # resources tied to the subject only by the deletion workflow


class AuditAgent:
    def __init__(self, dataset: Dataset):
        self.dataset = dataset
        self.evidence: Evidence = load_evidence(dataset.evidence)
        self.resolver = IdentityResolver(dataset)
        captured = {ev.file_id: ev.captured_at for ev in dataset.evidence}
        self.index = ResourceIndex(self.evidence, dataset.activity, captured)
        # Workflow batches mix sample and evaluation requests, so attribution sees both.
        all_requests = load_requests("sample_deletion_requests.csv") + load_requests("deletion_requests.csv")
        self.attributor = WorkflowAttributor(all_requests, self.resolver, self.index, dataset.activity)
        self.intent = IntentClassifier(all_requests)

    def context(self, request: Request) -> CaseContext:
        identity = self.resolver.resolve(request.email, request.audit_at)
        return CaseContext(
            request=request,
            identity=identity,
            links=self.index.linked(identity, request.audit_at),
            attribution=self.attributor.attribute(request),
        )

    def audit(self, request: Request) -> tuple[str, list[Step]]:
        if not self.intent.authorizes_deletion(request):
            return NOT_APPLICABLE, []
        ctx = self.context(request)
        decisions = plan_resource_actions(ctx, self.index, self.evidence, self.dataset.rules)
        return build_steps(decisions)
