"""Parser for the remaining records/*.json files.

    collection files   {"captured_at", "collection", "records": [typed records]}
                       (customer_analytics_report, customer_export_jobs)
    status ledgers     {"exported_at", "records": [{system, resource_id, record_type, result, ...}]}
                       (customer_export_manifest, connector_configurations, security_audit_ledger)
    snapshot bundles   {"exported_at", "snapshots": [{captured_at, data_store, collection, records}]}
                       mixing typed collections and operational_record_history status records

Each bundle snapshot carries its own capture time, which is the time that counts for state.
"""

import json

from ..data import EvidenceFile
from .model import Coverage, Observation
from .records import COLLECTION_SYSTEMS, collection_observation, is_status_record, status_record_observation


def _records(ev: EvidenceFile, records: list[dict], time: str, system_id: str | None, collection: str | None) -> list[Observation]:
    observations = []
    for record in records:
        if is_status_record(record):
            observations.append(status_record_observation(ev.file_id, time, record))
        elif collection:
            system = system_id if system_id in COLLECTION_SYSTEMS.values() else COLLECTION_SYSTEMS.get(collection, system_id)
            observations.append(collection_observation(ev.file_id, time, system, collection, record))
    return observations


def parse(ev: EvidenceFile) -> list[Observation | Coverage]:
    data = json.loads(ev.path.read_text(encoding="utf-8"))
    file_time = data.get("captured_at") or data.get("exported_at") or ev.captured_at
    if "snapshots" not in data:
        return _records(ev, data.get("records", []), file_time, None, data.get("collection"))

    items: list[Observation | Coverage] = []
    for snap in data["snapshots"]:
        observations = _records(ev, snap["records"], snap["captured_at"], snap.get("data_store"), snap.get("collection"))
        items += observations
        # A bundle snapshot of a typed collection lists that collection in full (an empty list proves
        # absence); operational_record_history snapshots are partial checklists and list nothing.
        if snap.get("collection") in COLLECTION_SYSTEMS:
            items.append(
                Coverage(
                    file_id=ev.file_id,
                    observed_at=snap["captured_at"],
                    system_id=COLLECTION_SYSTEMS[snap["collection"]],
                    table=f"{snap.get('data_store')}/{snap['collection']}",
                    resource_ids=frozenset(o.resource_id for o in observations),
                )
            )
    return items
