"""Link evidence resources to a resolved identity.

A resource belongs to the subject when any observation of it (or a data-catalog activity event)
carries one of the subject's identifiers: the stable user_ref, a current or historical email,
email hash or phone, including the key a lookup was made with. Links are only counted when they
were observable at or before the audit cutoff.
"""

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta

from .evidence import Evidence, Observation
from .identity import Identity, norm_email, norm_phone

ResourceKey = tuple[str, str]  # (system_id, resource_id)

_USER_REF = re.compile(r"\buser_\d+\b")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ALIAS = re.compile(r"(?:linked|confirmed) (\S+) \(([^)]+)\)")


@dataclass(frozen=True)
class Link:
    resource: ResourceKey
    source_id: str  # file_id or activity_id that shows the link
    at: str
    value: str  # normalized identifier that matched


def _normalize(value: str) -> str | None:
    value = str(value).strip()
    if not value:
        return None
    if _USER_REF.fullmatch(value):
        return value
    if "@" in value:
        return norm_email(value)
    if re.fullmatch(r"[0-9a-fA-F]{64}", value):
        return value.lower()
    digits = norm_phone(value)
    return f"tel:{digits}" if len(digits) >= 7 else None


def identity_values(identity: Identity) -> set[str]:
    values = set(identity.emails) | set(identity.email_hashes) | {f"tel:{p}" for p in identity.phones}
    if identity.user_id:
        values.add(identity.user_id)
    return values


class ResourceIndex:
    def __init__(self, evidence: Evidence, activity: list[dict], captured: dict[str, str] | None = None):
        """`captured` maps file_id -> the file's capture time from evidence_files.csv."""
        self.observations: dict[ResourceKey, list[Observation]] = defaultdict(list)
        self.captured = captured or {}
        self._links: dict[str, list[Link]] = defaultdict(list)

        # Catalog events name each resource's business alias ("object_19 (delivery_confirmation_19.pdf)").
        # Documents and screenshots show the alias, not the id; resolve them before anything else.
        self.aliases: dict[str, ResourceKey] = {}
        for row in activity:
            if row["event_type"] == "LINK_RESOURCE_IDENTITY" and (m := _ALIAS.search(row["details"])):
                if m.group(1) == row["resource_id"] and m.group(2) != row["resource_id"]:
                    self.aliases[m.group(2)] = (row["target_system"], row["resource_id"])
        for o in evidence.observations:
            if o.resource_id in self.aliases:
                o.system_id, o.resource_id = self.aliases[o.resource_id]
                o.attrs["alias_resolved"] = True

        for o in evidence.observations:
            key = (o.system_id, o.resource_id)
            self.observations[key].append(o)
            for raw in (o.user_ref, o.email, o.lookup_key, o.email_hash, o.phone):
                if raw and (value := _normalize(raw)):
                    # Ownership is a fact about the record, so the record's own time decides whether
                    # it was the subject's by the audit; proving its state additionally needs the file.
                    self._links[value].append(Link(key, o.file_id, o.observed_at, value))

        # What the deletion workflow tried to do to each resource (a hint about its kind, not its state).
        self.workflow_ops: dict[ResourceKey, set[str]] = defaultdict(set)
        for row in activity:
            if row["source_system"] == "deletion_worker":
                self.workflow_ops[(row["target_system"], row["resource_id"])].add(row["event_type"])
        for row in activity:
            if row["event_type"] != "LINK_RESOURCE_IDENTITY":
                continue
            key = (row["target_system"], row["resource_id"])
            for raw in _EMAIL.findall(row["details"]) + _USER_REF.findall(row["details"]):
                if value := _normalize(raw):
                    self._links[value].append(Link(key, row["activity_id"], row["event_time"], value))

    def available_at(self, item) -> str:
        """When an observation or coverage listing became knowable: its own time, but never before
        its file existed (a bundle exported 09-17 cannot inform a 09-15 audit, whatever it contains)."""
        return max(item.observed_at, self.captured.get(item.file_id, ""))

    def linked(self, identity: Identity, as_of: str) -> dict[ResourceKey, list[Link]]:
        """Resources linked to the identity by evidence available at or before as_of."""
        found: dict[ResourceKey, list[Link]] = defaultdict(list)
        for value in identity_values(identity):
            for link in self._links.get(value, ()):
                if link.at <= as_of:
                    found[link.resource].append(link)
        return dict(found)


# The deletion workflow reports each case's status from the result of its primary-profile delete.
_STATUS_RESULT = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
_WORKFLOW_LEAD = timedelta(minutes=80)  # workflow actions run 45-75 min before the status is reported
_WORKFLOW_LAG = timedelta(minutes=10)


def _ts(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _max_matching(graph: dict[str, set], skip_left: str | None = None, skip_right=None) -> int:
    """Size of a maximum bipartite matching (Kuhn's augmenting paths; graphs here are tiny)."""
    match_right: dict = {}

    def augment(left: str, seen: set) -> bool:
        for right in graph[left]:
            if right == skip_right or right in seen:
                continue
            seen.add(right)
            if right not in match_right or augment(match_right[right], seen):
                match_right[right] = left
                return True
        return False

    return sum(augment(left, set()) for left in sorted(graph) if left != skip_left)


def _decide_by_matching(graph: dict[str, set], subject: str) -> tuple[ResourceKey | None, set]:
    """The resource `subject` holds in every maximum matching, else (None, its possible resources)."""
    best = _max_matching(graph)
    always_matched = _max_matching(graph, skip_left=subject) == best - 1
    possible = {
        r for r in graph[subject]
        if _max_matching({k: v for k, v in graph.items() if k != subject}, skip_right=r) == best - 1
    }
    if always_matched and len(possible) == 1:
        return next(iter(possible)), possible
    return None, possible or set(graph[subject])


@dataclass
class Attribution:
    resources: dict[ResourceKey, str]  # workflow resource -> activity_id that acted on it for this case
    ambiguous: dict[str, list[ResourceKey]]  # target system -> tied candidates that could not be separated


class WorkflowAttributor:
    """Attribute deletion-workflow actions to cases when no evidence names the subject.

    Deleted and anonymized records often carry no identifier at all; the only trace tying them to a
    request is the deletion worker acting on them while that request was processed. Workflows run
    in batches, so for each case and target system the candidates are the worker's actions in the
    window before the case's reported_at, then narrowed by:
      * evidence: a candidate linked to this subject wins; one linked to another subject is excluded,
        and a subject whose evidence already shows its record in a system claims no unnamed ones there;
      * status: the primary-profile delete's reported_result matches the case's reported_status;
      * matching: across all cases sharing candidates, a resource goes to a subject only if every
        maximum matching assigns it there (see attribute()).
    Everything uses evidence available at the case's audit_at; ties are reported, not guessed.
    """

    def __init__(self, requests: list, resolver, index: ResourceIndex, activity: list[dict]):
        self.requests = requests
        self.resolver = resolver
        self.index = index
        self.actions = [a for a in activity if a["source_system"] == "deletion_worker"]
        self._action_times: dict[ResourceKey, list[datetime]] = defaultdict(list)
        self._acted: dict[ResourceKey, str] = {}
        for a in self.actions:
            key = (a["target_system"], a["resource_id"])
            self._action_times[key].append(_ts(a["event_time"]))
            self._acted.setdefault(key, a["activity_id"])
        self._owner_cache: dict[str, tuple] = {}
        self._candidate_cache: dict[tuple[str, str], dict] = {}

    def _window(self, request) -> list[dict]:
        reported = _ts(request.reported_at)
        return [a for a in self.actions if reported - _WORKFLOW_LEAD <= _ts(a["event_time"]) <= reported + _WORKFLOW_LAG]

    def _candidates(self, request, as_of: str, owners: dict[ResourceKey, set[str]]) -> dict[str, set[ResourceKey]]:
        cache_key = (as_of, request.case_id, request.email, request.reported_at, request.requested_at)
        if cache_key not in self._candidate_cache:
            self._candidate_cache[cache_key] = self._compute_candidates(request, as_of, owners)
        return self._candidate_cache[cache_key]

    def _evidenced_systems(self, user: str, request, as_of: str, owners) -> set[str]:
        """Systems where evidence gathered for this request already shows a record of the subject."""
        systems = set()
        for key, holders in owners.items():
            if user in holders and key[0] not in systems:
                if any(request.requested_at <= o.observed_at and self.index.available_at(o) <= as_of
                       for o in self.index.observations.get(key, ())):
                    systems.add(key[0])
        return systems

    def _compute_candidates(self, request, as_of: str, owners: dict[ResourceKey, set[str]]) -> dict[str, set[ResourceKey]]:
        user = self.resolver.resolve(request.email, as_of).user_id
        by_system: dict[str, list[dict]] = defaultdict(list)
        for action in self._window(request):
            by_system[action["target_system"]].append(action)
        evidenced = self._evidenced_systems(user, request, as_of, owners)
        candidates = {}
        for system, actions in by_system.items():
            keys = {(system, a["resource_id"]) for a in actions}
            mine = {k for k in keys if user in owners.get(k, ())}
            if mine:
                candidates[system] = mine
                continue
            if system in evidenced:
                continue  # evidence already shows this subject's record here: no claim on unnamed ones
            free = [a for a in actions if not owners.get((system, a["resource_id"]))]
            if system == "account_service":
                expected = _STATUS_RESULT.get(request.reported_status)
                matching = [a for a in free if a["reported_result"] == expected]
                free = matching or (free if len(free) == 1 else [])
            candidates[system] = {(system, a["resource_id"]) for a in free}
        return candidates

    def _owners(self, as_of: str) -> tuple[dict[str, str], dict[ResourceKey, set[str]]]:
        """Per cutoff (memoized): each case's user, and the subjects evidence links each resource to."""
        if as_of not in self._owner_cache:
            users, owners = {}, defaultdict(set)
            for r in self.requests:
                identity = self.resolver.resolve(r.email, as_of)
                users[r.case_id] = identity.user_id
                for key in self.index.linked(identity, as_of):
                    owners[key].add(identity.user_id)
            self._owner_cache[as_of] = (users, owners)
        return self._owner_cache[as_of]

    def _component(self, request, as_of: str, owners) -> dict[str, dict[str, set[ResourceKey]]]:
        """Candidate sets for the request and every case linked to it through shared candidates.

        Constraints propagate along such chains (a resource another case must use cannot be this
        case's), so the whole connected group is solved together.
        """
        cases = {request.case_id: request}
        cands = {request.case_id: self._candidates(request, as_of, owners)}
        frontier = [request.case_id]
        while frontier:
            cid = frontier.pop()
            for keys in cands[cid].values():
                for key in keys:
                    for t in self._action_times.get(key, ()):
                        for other in self.requests:  # cases whose workflow window contains this action
                            if other.case_id in cases or not (
                                _ts(other.reported_at) - _WORKFLOW_LEAD <= t <= _ts(other.reported_at) + _WORKFLOW_LAG
                            ):
                                continue
                            cases[other.case_id] = other
                            cands[other.case_id] = self._candidates(other, as_of, owners)
                            frontier.append(other.case_id)
        return cands

    def attribute(self, request) -> Attribution:
        """Resources the workflow acted on for this request's subject, decided by maximum matching.

        Per target system, subjects and candidate resources form a bipartite graph (a resource has
        one owner). A subject gets a resource only when every maximum matching gives it that same
        resource, e.g. two cases that between them need both of two accounts leave neither to a third
        case. Anything less certain is reported as a tie.
        """
        as_of = request.audit_at
        users, owners = self._owners(as_of)
        users = {**users, request.case_id: self.resolver.resolve(request.email, as_of).user_id}
        for r in self.requests:
            users.setdefault(r.case_id, self.resolver.resolve(r.email, as_of).user_id)
        cands = self._component(request, as_of, owners)

        me = users[request.case_id]
        resources, ambiguous = {}, {}
        systems = {s for sys_c in cands.values() for s in sys_c}
        for system in sorted(systems):
            graph: dict[str, set[ResourceKey]] = defaultdict(set)  # subject -> candidate resources
            for cid, sys_c in cands.items():
                graph[users[cid]] |= sys_c.get(system, set())
            if not graph.get(me):
                continue
            decided, possible = _decide_by_matching(graph, me)
            if decided:
                resources[decided] = self._acted[decided]
            else:
                ambiguous[system] = sorted(possible)
        return Attribution(resources=resources, ambiguous=ambiguous)
