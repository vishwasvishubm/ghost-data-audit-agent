"""Parser for logs/resource_lookup_trace_*.log: point-in-time readback of resource state.

Each lookup is a `*_started` line (carrying the lookup_key that was queried) and a `*_completed`
line (carrying the result), joined by trace_id.
"""

from ..data import EvidenceFile
from .logfmt import parse_line
from .model import Observation, identifiers_from

_STATE_KEYS = (
    "enabled", "last_delivery_status", "connector_name", "restore_allowed", "archive_key",
    "archive_present", "retention_until", "event_code", "object_key", "available",
    "audience_cohort", "http_status", "rows", "result", "total_hits", "index",
)


def _exists(fields: dict) -> bool:
    if fields.get("http_status") == 404 or fields.get("rows") == 0:
        return False
    if "result" in fields:
        return fields["result"] == "hit" and fields.get("total_hits", 1) > 0
    if fields.get("available") is False or fields.get("archive_present") is False:
        return False
    return True


def parse(ev: EvidenceFile) -> list[Observation]:
    lookup_keys: dict[str, str] = {}
    observations = []
    for line in ev.path.read_text(encoding="utf-8").splitlines():
        parsed = parse_line(line)
        if parsed is None:
            continue
        ts, fields = parsed
        event, trace = str(fields.get("event", "")), fields.get("trace_id")
        if event.endswith("_started") and trace:
            lookup_keys[trace] = fields.get("lookup_key")
        elif event.endswith("_completed") and "resource_id" in fields:
            attrs = {k: fields[k] for k in _STATE_KEYS if k in fields}
            attrs["event"] = event
            ids = identifiers_from(fields)
            ids["lookup_key"] = lookup_keys.get(trace) or ids["lookup_key"]
            observations.append(
                Observation(
                    file_id=ev.file_id,
                    observed_at=ts,
                    system_id=fields["service"],
                    resource_id=fields["resource_id"],
                    exists=_exists(fields),
                    attrs=attrs,
                    **ids,
                )
            )
    return observations
