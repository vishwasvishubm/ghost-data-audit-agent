"""Normalized observation of one resource's state, as seen in one evidence file at one moment."""

import re
from dataclasses import dataclass, field

USER_REF = re.compile(r"user_\d+")
_EMAIL_IN_TEXT = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_IN_TEXT = re.compile(r"\+\d[\d ()-]{6,}\d")

# Source field names for each identifier, across every evidence format.
_REFERENCE_KEYS = ("customer_reference", "subject_reference", "subject_selector", "user_id")
_EMAIL_KEYS = ("email", "owner_email", "requester_email", "customer_email", "uploaded_by")
_PHONE_KEYS = ("phone", "owner_phone", "requester_phone")
_NAME_KEYS = ("full_name", "display_name", "requester_name", "name")
_HASH_KEYS = ("email_hash",)
_TEXT_KEYS = ("message_text", "message_excerpt")


@dataclass
class Observation:
    file_id: str
    observed_at: str  # record-level time when the source gives one, else the file's capture time
    system_id: str
    resource_id: str
    exists: bool  # False when the source proves the record is gone (404, zero hits, ...)
    user_ref: str | None = None  # stable internal reference (user_123); allowed in tombstones and audit records
    email: str | None = None
    phone: str | None = None
    email_hash: str | None = None
    name: str | None = None
    lookup_key: str | None = None  # what the source was queried by / correlated on; links but is not exposed data
    attrs: dict = field(default_factory=dict)  # state signals: enabled, restore_allowed, data_category, ...

    @property
    def direct_identifiers(self) -> dict[str, str]:
        """Identifiers that policy may require removing (everything except the stable reference)."""
        fields = {"email": self.email, "phone": self.phone, "email_hash": self.email_hash, "name": self.name}
        return {k: v for k, v in fields.items() if v}


@dataclass(frozen=True)
class Coverage:
    """A full listing of one system's records in one table at one moment.

    A resource of `system_id` missing from `resource_ids` was not in that table at `observed_at`.
    Whether that proves deletion is a policy decision, not a parsing one.
    """

    file_id: str
    observed_at: str
    system_id: str
    table: str
    resource_ids: frozenset[str]


# Checklist status vocabulary shared by handover files and record_lookup log events:
# STATUS -> (exists, extra state attrs).
_STATUS = {
    "NOT_FOUND": (False, {}),
    "FOUND": (True, {}),
    "IDENTIFIABLE": (True, {}),
    "ANONYMIZED": (True, {}),
    "REDACTED": (True, {}),
    "ACTIVE": (True, {}),
    "DISABLED": (True, {"enabled": False}),
    "BLOCKED": (True, {"restore_allowed": False}),
}


def status_observation(status: str, system_id: str, category: str, identifier: str | None) -> dict:
    """Observation kwargs (exists, attrs, identifiers) for a checklist-style status line."""
    exists, state = _STATUS.get(status, (True, {}))
    attrs = {"data_category": category, "status": status, **state}
    if status == "ACTIVE":
        attrs["restore_allowed" if system_id == "backup_service" else "enabled"] = True
    kwargs: dict = {"exists": exists, "attrs": attrs}
    if identifier and USER_REF.fullmatch(identifier):
        kwargs["user_ref"] = identifier
    elif identifier and "@" in identifier:
        # On an absent or de-identified record the email only labels what was looked up.
        exposed = exists and status not in ("ANONYMIZED", "REDACTED")
        kwargs["email" if exposed else "lookup_key"] = identifier
    return kwargs


def _first(record: dict, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        if record.get(key):
            return str(record[key])
    return None


def identifiers_from(record: dict) -> dict:
    """Pull normalized identifier fields out of a raw source record.

    Reference fields hold either a stable user_NN (kept as user_ref) or an email/hash that only
    selects the subject (kept as lookup_key). Free-text fields are scanned for embedded PII.
    """
    reference = _first(record, _REFERENCE_KEYS)
    text = " ".join(str(record[k]) for k in _TEXT_KEYS if record.get(k))
    email_in_text = _EMAIL_IN_TEXT.search(text)
    phone_in_text = _PHONE_IN_TEXT.search(text)
    return {
        "user_ref": reference if reference and USER_REF.fullmatch(reference) else None,
        "lookup_key": reference if reference and not USER_REF.fullmatch(reference) else None,
        "email": _first(record, _EMAIL_KEYS) or (email_in_text.group(0) if email_in_text else None),
        "phone": _first(record, _PHONE_KEYS) or (phone_in_text.group(0) if phone_in_text else None),
        "email_hash": _first(record, _HASH_KEYS),
        "name": _first(record, _NAME_KEYS),
    }
