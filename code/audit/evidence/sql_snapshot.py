"""Parser for databases/*.sql: SQLite point-in-time snapshots.

Three shapes occur:
  * topology snapshots: a *_primary table plus replica tables (read, regional, reporting,
    failover), rows holding origin_system + collection_name + record_json;
  * typed topology snapshots: customer_profiles_* and search_documents_primary;
  * inventory snapshots: one typed table per system plus snapshot_scope(origin_system, captured_at).

Every row becomes an Observation of its origin system, tagged with the table it came from, so a
copy that survives only in a replica is still a copy of that system's record. Each table also
yields a Coverage listing per system, for absence-by-omission reasoning in the policy step.
"""

import json
import re
import sqlite3

from ..data import EvidenceFile
from .model import Coverage, Observation
from .records import COLLECTION_SYSTEMS, collection_observation

_CAPTURED = re.compile(r"^--\s*Captured at:\s*(\S+)", re.MULTILINE)

# Typed topology tables -> (system, collection they hold).
_TYPED_TABLES = {
    "customer_profiles_primary": ("account_service", "customer_profiles"),
    "customer_profiles_failover_replica": ("account_service", "customer_profiles"),
    "search_documents_primary": ("search_service", "customer_search_documents"),
}


def _role(table: str) -> str:
    """primary, read_replica, replica_ap_south, failover_replica, ... (inventory tables: 'inventory')."""
    for marker in ("_primary", "_failover_replica", "_reporting_replica", "_read_replica"):
        if table.endswith(marker):
            return marker[1:]
    if m := re.search(r"_replica_(\w+)$", table):
        return f"replica_{m.group(1)}"
    return "inventory"


def _table_system(table: str) -> str | None:
    return _TYPED_TABLES[table][0] if table in _TYPED_TABLES else COLLECTION_SYSTEMS.get(table)


def parse(ev: EvidenceFile) -> list[Observation | Coverage]:
    text = ev.path.read_text(encoding="utf-8")
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(text)

    header = _CAPTURED.search(text)
    file_time = header.group(1) if header else ev.captured_at
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    scope = dict(db.execute("SELECT origin_system, captured_at FROM snapshot_scope").fetchall()) if "snapshot_scope" in tables else None
    tables = [t for t in tables if t != "snapshot_scope"]

    items: list[Observation | Coverage] = []
    listed: dict[tuple[str, str], set[str]] = {}
    for table in tables:
        for row in db.execute(f'SELECT * FROM "{table}"'):
            record = dict(row)
            if "record_json" in record:
                system_id, collection = record["origin_system"], record["collection_name"]
                record = json.loads(record["record_json"])
            else:
                system_id = _table_system(table)
                collection = _TYPED_TABLES[table][1] if table in _TYPED_TABLES else table
            listed.setdefault((table, system_id), set()).add(record["resource_id"])
            items.append(
                collection_observation(
                    ev.file_id, (scope or {}).get(system_id, file_time), system_id, collection, record,
                    table=table, role=_role(table),
                )
            )

    # Coverage: inventories declare their scope; topology tables cover every system seen in the file.
    systems = set(scope) if scope is not None else {s for _, s in listed} | {_table_system(t) for t in tables} - {None}
    for table in tables:
        table_systems = {_table_system(table)} if _table_system(table) else systems
        for system_id in table_systems & systems:
            items.append(
                Coverage(
                    file_id=ev.file_id,
                    observed_at=(scope or {}).get(system_id, file_time),
                    system_id=system_id,
                    table=table,
                    resource_ids=frozenset(listed.get((table, system_id), set())),
                )
            )
    return items
