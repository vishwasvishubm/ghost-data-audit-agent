"""Parser for the central observability exports and other service logs under logs/.

Lines come in several envelopes (see envelopes.py) but share one event model. Lookups are traced:
a `*_started` event names the key that was queried, later events in the same trace report what was
found. Only events that establish a resource's state become observations; infrastructure noise
(connection pools, retries, certificates, ...) is ignored.
"""

from collections.abc import Callable

from ..data import EvidenceFile
from .envelopes import LogEvent, parse_event
from .model import USER_REF, Observation, status_observation

# Services that report on a replica of another system's data.
_SYSTEM_ALIASES = {"support_replica": "support_platform"}
# Fields on a *_started event that name what was queried.
_QUERY_KEYS = ("lookup_key", "match_key", "subject_key", "scan_key", "owner_key", "subject_ref")
# Fields on any event that link the resource to a subject.
_LINK_KEYS = ("correlation_key", "subject_reference", "subject_ref", "lookup_key")


def _exposed(value: str | None) -> dict:
    if not value or USER_REF.fullmatch(str(value)):
        return {}
    return {"email": value} if "@" in str(value) else {"phone": value}


# event name -> builder(fields) returning Observation kwargs beyond the common ones.
_BUILDERS: dict[str, Callable[[dict], dict]] = {
    "profile_lookup_completed": lambda f: {
        "exists": f.get("http_status") != 404 and f.get("rows") != 0,
        "attrs": {"http_status": f.get("http_status")},
    },
    "audit_record_read_completed": lambda f: {
        "exists": f.get("rows") != 0,
        "attrs": {"data_category": "SECURITY_AUDIT_LOG", "integrity_check": f.get("integrity_check")},
    },
    "index_query_completed": lambda f: {"exists": f.get("result") == "hit", "attrs": {"index": f.get("index")}},
    "message_pii_scan": lambda f: {"attrs": {"data_category": "SUPPORT_MESSAGE"}, **_exposed(f.get("matched_value"))},
    "message_read": lambda f: {
        "attrs": {"data_category": "SUPPORT_MESSAGE", "redacted": f.get("message_text") == "[REDACTED]"},
    },
    "attachment_pii_scan": lambda f: {
        "attrs": {"data_category": "SUPPORT_ATTACHMENT", "file": f.get("file")},
        **_exposed(f.get("matched_value")),
    },
    "analytics_profile_read": lambda f: {
        "attrs": {"data_category": "ANALYTICS_PROFILE", "direct_identifiers": f.get("direct_identifiers")},
        **{k: f[k] for k in ("email", "phone") if f.get(k)},
    },
    "analytics_identity_scan": lambda f: {
        "attrs": {"data_category": "ANALYTICS_PROFILE", "direct_identifiers": f.get("direct_identifiers")},
        **_exposed(f.get("matched_value")),
    },
    "contact_lookup_completed": lambda f: {
        "exists": f.get("rows") != 0,
        "attrs": {"email_opt_in": f.get("email_opt_in")},
    },
    "contact_query_completed": lambda f: {"exists": f.get("rows") != 0, "attrs": {"source": f.get("source")}},
    "deletion_marker_check": lambda f: {"attrs": {"marker_found": f.get("marker_found"), "last_write": f.get("last_write")}},
    "connector_heartbeat": lambda f: {
        "attrs": {"enabled": bool(f.get("enabled")), "scheduler_registered": f.get("scheduler_registered")},
    },
    "connector_config_loaded": lambda f: {
        "attrs": {"enabled": bool(f.get("enabled")), "scheduler_registered": f.get("scheduler_registered")},
    },
    "restore_candidate_evaluated": lambda f: {
        "attrs": {"restore_allowed": bool(f.get("eligible")) and not f.get("deletion_block")},
    },
    "restore_authorization_completed": lambda f: {
        "attrs": {"restore_allowed": f.get("decision") != "deny", "policy": f.get("policy")},
    },
    "export_download_check_completed": lambda f: {"exists": f.get("available") is not False, "attrs": {"file": f.get("file")}},
    "retained_order_loaded": lambda f: {
        "attrs": {"data_category": "LEGAL_HOLD_RECORD", "reason": f.get("reason"), "access": f.get("access")},
    },
}


def _link(fields: dict, query_key: str | None) -> dict:
    """Who the resource belongs to: a stable user_ref, or an email/hash kept as lookup_key."""
    key = next((fields[k] for k in _LINK_KEYS if fields.get(k)), None) or query_key
    if not key:
        return {}
    return {"user_ref": key} if USER_REF.fullmatch(str(key)) else {"lookup_key": str(key)}


def _observe(ev: EvidenceFile, event: LogEvent, query_key: str | None) -> Observation | None:
    f = event.fields
    if "resource_id" not in f:
        return None
    system_id = str(f.get("system") or _SYSTEM_ALIASES.get(event.service, event.service))
    common = {"file_id": ev.file_id, "observed_at": event.ts, "system_id": system_id, "resource_id": str(f["resource_id"])}

    if event.event == "record_lookup":  # checklist-style line with its own system and category
        kwargs = status_observation(str(f.get("result")), system_id, str(f.get("record_type")), f.get("subject_reference"))
        return Observation(**common, **kwargs)

    build = _BUILDERS.get(event.event)
    if build is None:
        return None
    kwargs = {"exists": True, **build(f), **_link(f, query_key)}
    kwargs["attrs"] = {"event": event.event, **kwargs["attrs"]}
    return Observation(**common, **kwargs)


def parse(ev: EvidenceFile) -> list[Observation]:
    query_keys: dict[str, str] = {}
    observations = []
    for line in ev.path.read_text(encoding="utf-8").splitlines():
        event = parse_event(line)
        if event is None:
            continue
        if event.event.endswith("_started") and event.trace_id:
            key = next((event.fields[k] for k in _QUERY_KEYS if event.fields.get(k)), None)
            if key:
                query_keys[event.trace_id] = str(key)
            continue
        query_key = query_keys.get(event.trace_id) if event.trace_id else None
        if obs := _observe(ev, event, query_key):
            observations.append(obs)
    return observations
