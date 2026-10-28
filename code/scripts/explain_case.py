"""Show how the agent sees one case: identity, linked and workflow resources, and each decision.

    python code/scripts/explain_case.py case_26 [--requests sample_deletion_requests.csv]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.agent import AuditAgent  # noqa: E402
from audit.data import load_dataset, load_requests  # noqa: E402
from audit.policy import _decide_resource, category_of, load_rules, state_at  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("case_id")
    parser.add_argument("--requests", default="sample_deletion_requests.csv")
    args = parser.parse_args()

    agent = AuditAgent(load_dataset())
    request = next(r for r in load_requests(args.requests) if r.case_id == args.case_id)
    ctx = agent.context(request)
    actions = load_rules(agent.dataset.rules)
    print(f"{request.case_id} {request.email} requested={request.requested_at} audit={request.audit_at} reported={request.reported_status}")
    print(f"identity {ctx.identity.user_id} emails={sorted(ctx.identity.emails)}")
    print(f"workflow: {ctx.attribution.resources}  ties: {ctx.attribution.ambiguous}")
    for key in sorted(set(ctx.links) | set(ctx.attribution.resources)):
        obs = agent.index.observations.get(key, [])
        state = state_at(key, obs, agent.evidence.coverage, request.audit_at, agent.index.available_at)
        decision = _decide_resource(key, ctx, agent.index, agent.evidence, actions)
        via = sorted({link.source_id for link in ctx.links.get(key, [])}) or ["workflow"]
        print(f"  {key[0]:20} {key[1]:18} cat={category_of(key, obs)} via={via}")
        print(f"      state={state and (state.exists, state.evidence_id, state.at)}  -> {decision and (decision.outcome, decision.evidence_id)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
