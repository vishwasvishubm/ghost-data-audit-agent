"""Shared builders for structured records found in SQL snapshots and JSON record files.

Two record kinds recur across formats:
  * collection records: a typed business record (profile, search document, connector, ...)
    from a named collection, e.g. {"resource_id": ..., "email": ..., "enabled": true};
  * status records: a checklist entry about some resource,
    e.g. {"system": ..., "resource_id": ..., "record_type": ..., "result": "FOUND", "subject_reference": ...}.
"""

from .model import Observation, identifiers_from, status_observation

# Collection / typed table -> system, for sources that name only the collection.
COLLECTION_SYSTEMS = {
    "customer_profiles": "account_service",
    "deleted_customer_references": "identity_service",
    "customer_search_documents": "search_service",
    "customer_analytics_profiles": "analytics_platform",
    "customer_analytics_reports": "analytics_platform",
    "support_conversations": "support_platform",
    "support_records": "support_platform",
    "ticket_attachments": "support_platform",
    "customer_exports": "export_service",
    "customer_export_jobs": "export_service",
    "connector_configurations": "integration_service",
    "connector_subscriptions": "integration_service",
    "backup_catalog": "backup_service",
    "backup_restore_entries": "backup_service",
    "security_audit_events": "security_service",
    "security_audit_records": "security_service",
    "retained_order_records": "legal_service",
}

# Collection / typed table -> deletion_rules.csv data category (data flows have no rule category).
COLLECTION_CATEGORIES = {
    "customer_profiles": "USER_PROFILE",
    "deleted_customer_references": "ACCOUNT_TOMBSTONE",
    "customer_search_documents": "SEARCH_INDEX_ENTRY",
    "customer_analytics_profiles": "ANALYTICS_PROFILE",
    "customer_analytics_reports": "ANALYTICS_PROFILE",
    "support_conversations": "SUPPORT_MESSAGE",
    "support_records": "SUPPORT_MESSAGE",
    "ticket_attachments": "SUPPORT_ATTACHMENT",
    "customer_exports": "USER_EXPORT",
    "customer_export_jobs": "USER_EXPORT",
    "backup_catalog": "BACKUP_ARCHIVE",
    "backup_restore_entries": "BACKUP_ARCHIVE",
    "security_audit_events": "SECURITY_AUDIT_LOG",
    "security_audit_records": "SECURITY_AUDIT_LOG",
    "retained_order_records": "LEGAL_HOLD_RECORD",
}

_STATE_KEYS = (
    "account_status", "enabled", "restore_allowed", "download_available", "retention_until",
    "retained_until", "retention_reason", "connector_name", "destination", "mode", "archive_path",
    "archive_key", "object_key", "file_name", "source_resource_id", "event_code", "event_name",
    "index_name", "message_text", "message_excerpt", "expires_at",
)


def normalize(record: dict) -> dict:
    """Flatten nested search documents and turn 0/1 flags into booleans."""
    record = dict(record)
    if isinstance(record.get("document"), dict):
        record = {**record.pop("document"), **record}
    for key in ("enabled", "restore_allowed", "download_available"):
        if isinstance(record.get(key), int) and not isinstance(record.get(key), bool):
            record[key] = bool(record[key])
    return record


def collection_observation(file_id: str, observed_at: str, system_id: str, collection: str, record: dict, **attrs) -> Observation:
    record = normalize(record)
    return Observation(
        file_id=file_id,
        observed_at=observed_at,
        system_id=system_id,
        resource_id=record["resource_id"],
        exists=True,
        attrs={
            "collection": collection,
            "data_category": COLLECTION_CATEGORIES.get(collection),
            **attrs,
            **{k: record[k] for k in _STATE_KEYS if k in record},
        },
        **identifiers_from(record),
    )


def is_status_record(record: dict) -> bool:
    return {"system", "resource_id", "record_type", "result"} <= record.keys()


def status_record_observation(file_id: str, default_time: str, record: dict) -> Observation:
    subject = record.get("subject_reference") or record.get("lookup_value")
    return Observation(
        file_id=file_id,
        observed_at=record.get("recorded_at") or default_time,
        system_id=record["system"],
        resource_id=record["resource_id"],
        **status_observation(record["result"], record["system"], record["record_type"], subject),
    )
