"""Stage 4: decide each linked resource's state at audit_at and the treatment deletion_rules.csv requires.

State comes from the latest evidence at or before audit_at: the newest observation(s) of the
resource, or a later full-table snapshot that no longer lists it. Workflow results are claims and
never establish state on their own; a resource whose state no evidence shows yields no finding.
"""

import re
from dataclasses import dataclass

from .evidence import Evidence, Observation
from .evidence.sql_snapshot import _role as sql_role
from .linking import ResourceIndex, ResourceKey

# Rule action per data category (deletion_rules.csv), plus the two process kinds the rules imply.
DELETE, REDACT, ANONYMIZE, RETAIN, BLOCK, DISABLE = "DELETE", "REDACT", "ANONYMIZE", "RETAIN", "BLOCK", "DISABLE"

# Default category per system, for observations whose source does not state one.
_SYSTEM_CATEGORY = {
    "account_service": "USER_PROFILE",
    "search_service": "SEARCH_INDEX_ENTRY",
    "cache_service": "CACHE_ENTRY",
    "analytics_platform": "ANALYTICS_PROFILE",
    "support_platform": "SUPPORT_MESSAGE",
    "export_service": "USER_EXPORT",
    "marketing_platform": "MARKETING_PROFILE",
    "email_delivery_service": "EMAIL_DELIVERY_LOG",
    "security_service": "SECURITY_AUDIT_LOG",
    "legal_service": "LEGAL_HOLD_RECORD",
    "backup_service": "BACKUP_ARCHIVE",
    "identity_service": "ACCOUNT_TOMBSTONE",
    "integration_service": "DATA_FLOW",
}

# Operations for (finding, remediation, verification) per treatment.
_OPS = {
    "REMAINING": ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    "ABSENT": (None, None, "VERIFY_RECORD_ABSENT"),
    "EXPOSED_REDACT": ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    "REDACTED": (None, None, "VERIFY_RECORD_REDACTED"),
    "EXPOSED_ANONYMIZE": ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    "ANONYMIZED": (None, None, "VERIFY_RECORD_ANONYMIZED"),
    "RETAINED": (None, None, "VERIFY_RECORD_PRESENT"),
    "RESTORE_RISK": ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
    "BLOCKED": (None, None, "VERIFY_RESTORE_BLOCKED"),
    "ACTIVE_FLOW": ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    "DISABLED": (None, None, "VERIFY_DATA_FLOW_DISABLED"),
}


@dataclass
class Decision:
    resource: ResourceKey
    category: str
    outcome: str  # key of _OPS
    evidence_id: str  # file/activity proving the observed state ("" when no evidence shows it)

    @property
    def violation(self) -> bool:
        return _OPS[self.outcome][0] is not None

    def ops(self) -> tuple[str | None, str | None, str]:
        return _OPS[self.outcome]


def load_rules(rules: list[dict]) -> dict[str, str]:
    actions = {r["data_category"]: r["required_action"] for r in rules}
    actions.setdefault("DATA_FLOW", DISABLE)  # sync/import paths must be inactive (decision rules)
    actions["BACKUP_ARCHIVE"] = BLOCK if actions.get("BACKUP_ARCHIVE") == RETAIN else actions.get("BACKUP_ARCHIVE", BLOCK)
    return actions


def category_of(key: ResourceKey, observations: list[Observation], workflow_ops: set[str] = frozenset()) -> str | None:
    """The record's data category: what its sources say, else the system default.

    Where no source states a category, a support record the deletion workflow tried to delete
    outright is an attachment-type record (messages are redacted, never deleted, by policy).
    """
    for o in sorted(observations, key=lambda o: o.observed_at, reverse=True):
        if o.attrs.get("event") == "contact_lookup_completed":
            return "SECONDARY_IDENTIFIER"  # a contact record keyed by a (former) email or hash
        if o.attrs.get("data_category"):
            return o.attrs["data_category"]
    if key[0] == "support_platform" and "DELETE_RECORD" in workflow_ops:
        return "SUPPORT_ATTACHMENT"
    return _SYSTEM_CATEGORY.get(key[0])


@dataclass
class State:
    exists: bool
    observations: list[Observation]  # the latest observations establishing the state
    evidence_id: str
    at: str  # time of the evidence that settled the state


def _primary_or(role: str | None) -> str:
    return "primary" if role in (None, "", "primary", "inventory") else role


def _observation_copy(o: Observation) -> str:
    """Which physical copy an observation saw: the service itself, or a named replica."""
    return _primary_or(o.attrs.get("role"))


def _coverage_copy(c) -> str:
    return _primary_or(sql_role(c.table)) if "/" not in c.table else "primary"


def state_at(key: ResourceKey, observations: list[Observation], coverage: list, as_of: str, available,
             use_omissions: bool = True) -> State | None:
    """The resource's state at as_of from evidence knowable by then; None if nothing shows it.

    Each copy (the service, each replica) keeps its own latest state, from sightings, explicit
    not-found results and (for records that must be deleted) full listings that omit it. The
    record exists if any copy still holds it. Absence is cited from an explicit not-found result
    when one was seen after the last sighting, else from the listing.
    """
    events: dict[str, list[tuple[str, str, object]]] = {}
    for o in observations:
        if available(o) <= as_of:
            events.setdefault(_observation_copy(o), []).append((o.observed_at, "seen" if o.exists else "gone", o))
    if use_omissions:
        for c in coverage:
            copy = _coverage_copy(c)
            # Listings are filtered views; they only speak for copies no direct read has covered.
            if copy not in events or all(kind == "omitted" for _, kind, _ in events[copy]):
                if c.system_id == key[0] and key[1] not in c.resource_ids and available(c) <= as_of:
                    events.setdefault(copy, []).append((c.observed_at, "omitted", c))
    if not events:
        return None

    present, absent = [], []
    for evs in events.values():
        latest = max(t for t, _, _ in evs)
        if any(kind == "seen" for t, kind, _ in evs if t == latest):
            present += [(t, item) for t, kind, item in evs if t == latest and kind == "seen"]
        else:
            last_seen = max((t for t, kind, _ in evs if kind == "seen"), default="")
            absent += [(t, kind, item) for t, kind, item in evs if t > last_seen]

    if present:
        newest = max(t for t, _ in present)
        chosen = [o for t, o in present if t == newest]
        return State(True, chosen, min(o.file_id for o in chosen), newest)
    direct = [(t, item) for t, kind, item in absent if kind == "gone"]
    t, item = max(direct or [(t, item) for t, _, item in absent], key=lambda x: (x[0], x[1].file_id))
    return State(False, [], item.file_id, t)


def _exposed(observations: list[Observation], stable_ref_allowed: bool) -> bool:
    for o in observations:
        if o.direct_identifiers or o.attrs.get("redacted") is False:
            return True
        if not stable_ref_allowed and o.user_ref:
            return True
    return False


def decide(key: ResourceKey, category: str, action: str, state: State) -> Decision | None:
    obs, ev = state.observations, state.evidence_id
    if action == DELETE:
        return Decision(key, category, "REMAINING" if state.exists else "ABSENT", ev)
    if not state.exists:
        return None  # nothing left to redact, anonymize, retain, block or disable
    if action == REDACT:
        return Decision(key, category, "EXPOSED_REDACT" if _exposed(obs, stable_ref_allowed=True) else "REDACTED", ev)
    if action == ANONYMIZE:
        return Decision(key, category, "EXPOSED_ANONYMIZE" if _exposed(obs, stable_ref_allowed=False) else "ANONYMIZED", ev)
    if action == RETAIN:
        return Decision(key, category, "RETAINED", ev)
    if action == BLOCK:
        # Retaining a backup is only allowed once its restore access is blocked; an archive that
        # exists with no evidence of a block is still a restore path.
        allowed = [o.attrs["restore_allowed"] for o in obs if "restore_allowed" in o.attrs]
        return Decision(key, category, "RESTORE_RISK" if any(allowed) or not allowed else "BLOCKED", ev)
    if action == DISABLE:
        enabled = [o.attrs["enabled"] for o in obs if "enabled" in o.attrs]
        return Decision(key, category, "ACTIVE_FLOW" if any(enabled) else "DISABLED", ev) if enabled else None
    return None


_RESOURCE_ID = re.compile(r"[a-z]+(?:_[a-z]+)*_\d+")


def _retention_lapsed(observations: list[Observation], as_of: str, available) -> bool:
    """True when the latest known retention date (security log retention, legal hold) passed by as_of."""
    dates = [
        (o.observed_at, str(o.attrs.get("retention_until") or o.attrs.get("retained_until")))
        for o in observations
        if available(o) <= as_of and (o.attrs.get("retention_until") or o.attrs.get("retained_until"))
    ]
    return bool(dates) and max(dates)[1] < as_of


def _decide_resource(key, ctx, index: ResourceIndex, evidence: Evidence, actions: dict) -> Decision | None:
    if not _RESOURCE_ID.fullmatch(key[1]):
        return None  # a business alias (file name, document number) the catalog never resolved
    observations = index.observations.get(key, [])
    category = category_of(key, observations, index.workflow_ops.get(key, set()))
    action = actions.get(category)
    if action is None:
        return None  # systems outside the policy (object storage listings, profile_service, ...)
    if action == RETAIN and _retention_lapsed(observations, ctx.request.audit_at, index.available_at):
        action = DELETE  # rules 12/13 only keep records within retention / under an active hold
    if observations and all(o.attrs.get("derived_from") for o in observations):
        return None  # ids only inferred from an order number: not grounded enough to act on
    # A listing that lacks a record can prove a deletion, but never that a retained record vanished:
    # listings of security events and the like are filtered views, not the whole store.
    state = state_at(key, observations, evidence.coverage, ctx.request.audit_at, index.available_at,
                     use_omissions=action == DELETE)
    if state is None:
        if key in ctx.attribution.resources and action == DELETE:
            return Decision(key, category, "ABSENT", "")  # known only from the workflow: verify, cannot cite
        if action == RETAIN and key in ctx.links:
            return Decision(key, category, "RETAINED", "")  # the subject's retained record; no citable state yet
        return None
    if state.at < ctx.request.requested_at and key not in ctx.attribution.resources:
        # Evidence from before the request says nothing about how the request was handled: a record
        # last seen months earlier (or already gone by then) is not part of this audit's findings.
        return None
    return decide(key, category, action, state)


def plan_resource_actions(ctx, index: ResourceIndex, evidence: Evidence, rules: list[dict]) -> list[Decision]:
    actions = load_rules(rules)
    decisions = [d for key in sorted(ctx.links) if (d := _decide_resource(key, ctx, index, evidence, actions))]
    # The workflow only fills gaps: when evidence already shows this subject's record in a system,
    # a record the workflow touched there may be someone else's (or the wrong one entirely).
    covered = {d.resource[0] for d in decisions}
    for key in sorted(ctx.attribution.resources):
        if key[0] not in covered and key not in ctx.links:
            if d := _decide_resource(key, ctx, index, evidence, actions):
                decisions.append(d)
    return decisions
