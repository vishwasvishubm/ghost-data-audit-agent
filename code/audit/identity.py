"""Stage 2: resolve the request email to a user and all identifiers they have had up to audit_at.

The request email may be current or historical. Matching is exact (case-insensitive for emails,
digits-only for phones); names are never used, because the evidence contains decoy customers
with near-identical names and emails.
"""

import hashlib
import re
from dataclasses import dataclass, field

from .data import Dataset


def norm_email(value: str) -> str:
    return value.strip().lower()


def norm_phone(value: str) -> str:
    return re.sub(r"\D", "", value)


def email_hashes(email: str) -> set[str]:
    return {hashlib.sha256(variant.encode("utf-8")).hexdigest() for variant in (email, email.lower())}


@dataclass
class Identity:
    user_id: str | None
    emails: set[str] = field(default_factory=set)
    phones: set[str] = field(default_factory=set)
    email_hashes: set[str] = field(default_factory=set)
    names: set[str] = field(default_factory=set)
    audit_ids: list[str] = field(default_factory=list)  # audit_log rows that contributed historical values

    def matches(self, value: str | None) -> bool:
        """True if a raw identifier value (user ref, email, hash or phone) belongs to this identity."""
        if not value:
            return False
        value = str(value).strip()
        if value == self.user_id:
            return True
        if "@" in value:
            return norm_email(value) in self.emails
        if re.fullmatch(r"[0-9a-f]{64}", value.lower()):
            return value.lower() in self.email_hashes
        digits = norm_phone(value)
        return len(digits) >= 7 and digits in self.phones


class IdentityResolver:
    def __init__(self, dataset: Dataset):
        self.current = {row["user_id"]: row for row in dataset.identities}
        self.history: dict[str, list[dict]] = {}
        for row in sorted(dataset.audit_log, key=lambda r: r["event_time"]):
            self.history.setdefault(row["user_id"], []).append(row)
        self.by_email: dict[str, set[str]] = {}
        for row in dataset.identities:
            self.by_email.setdefault(norm_email(row["email"]), set()).add(row["user_id"])
        for row in dataset.audit_log:
            if row["field_name"] == "email":
                for value in (row["old_value"], row["new_value"]):
                    self.by_email.setdefault(norm_email(value), set()).add(row["user_id"])

    def resolve(self, email: str, as_of: str) -> Identity:
        candidates = sorted(self.by_email.get(norm_email(email), set()))
        if not candidates:
            identity = Identity(user_id=None, emails={norm_email(email)})
            identity.email_hashes |= email_hashes(email)
            return identity
        # Exact matches are unique in this dataset; prefer a user whose email at as_of is the request email.
        return self._identity(candidates[0], as_of)

    def _identity(self, user_id: str, as_of: str) -> Identity:
        row = self.current[user_id]
        values = {"email": {row["email"]}, "phone": {row["phone"]}, "email_hash": {row["email_hash"]}}
        identity = Identity(user_id=user_id, names={row["name"]})
        for change in self.history.get(user_id, []):
            if change["event_time"] > as_of:
                # Not yet known at audit time: the snapshot's newer value did not exist then.
                values.get(change["field_name"], set()).discard(change["new_value"])
                values.setdefault(change["field_name"], set()).add(change["old_value"])
                continue
            values.setdefault(change["field_name"], set()).update((change["old_value"], change["new_value"]))
            identity.audit_ids.append(change["audit_id"])

        identity.emails = {norm_email(v) for v in values.get("email", ()) if v}
        identity.phones = {norm_phone(v) for v in values.get("phone", ()) if v}
        identity.email_hashes = {v.lower() for v in values.get("email_hash", ()) if v}
        for e in identity.emails:
            identity.email_hashes |= email_hashes(e)
        return identity


def resolve_identity(email: str, dataset: Dataset, as_of: str) -> Identity:
    return IdentityResolver(dataset).resolve(email, as_of)
