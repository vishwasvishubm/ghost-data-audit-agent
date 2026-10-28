"""Parser for records/service_resource_export_*.json: per-service readback of resource state."""

import json

from ..data import EvidenceFile
from .model import Observation, identifiers_from

# Fields that describe state rather than identity; kept in Observation.attrs.
_STATE_KEYS = (
    "enabled", "last_delivery_status", "connector_name", "restore_allowed", "archive_key",
    "retention_until", "event_code", "object_key", "audience_cohort", "message_excerpt",
)


def parse(ev: EvidenceFile) -> list[Observation]:
    data = json.loads(ev.path.read_text(encoding="utf-8"))
    observations = []
    for system_id, collections in data["service_exports"].items():
        for collection, items in collections.items():
            for item in items:
                observations.append(_observe(ev, system_id, collection, item))
    return observations


def _observe(ev: EvidenceFile, system_id: str, collection: str, item: dict) -> Observation:
    attrs = {"collection": collection, **{k: item[k] for k in _STATE_KEYS if k in item}}
    exists, record = True, item

    if "status_code" in item:  # account profile lookup: 404 proves absence
        attrs["status_code"] = item["status_code"]
        exists = item["status_code"] != 404 and item.get("profile") is not None
        record = item.get("profile") or {}
    elif "hits" in item:  # search query: the matched document carries the identifiers
        docs = [d for d in item["hits"]["documents"] if d.get("resource_id") == item["resource_id"]]
        exists = item["hits"]["total"] > 0 and bool(docs)
        record = docs[0] if docs else {}
        attrs["query"] = item.get("query")

    ids = identifiers_from(record)
    ids["lookup_key"] = ids["lookup_key"] or item.get("query")
    return Observation(
        file_id=ev.file_id,
        observed_at=item.get("observed_at") or ev.captured_at,
        system_id=system_id,
        resource_id=item["resource_id"],
        exists=exists,
        attrs=attrs,
        **ids,
    )
