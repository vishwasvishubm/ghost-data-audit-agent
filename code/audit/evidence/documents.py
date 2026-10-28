"""Parser for documents/*.pdf, using each PDF's text layer (no OCR or model needed).

The PDFs are ordinary business documents, recognised by their title line:

    DELIVERY ADDRESS CORRECTION REQUEST       support attachment, names its support_NNN resource
    COMPLIANCE JOURNAL - SIGNED RECORD ...    security audit entry, names its audit_record_NNN
    DISPUTED ORDERS AND TAX RECORDS REGISTER  legal-retention table: ORD | user | reason | ... | Retain until
    CUSTOMER STATEMENT                         statement exposing a customer's details for one order

A document without a resource id is emitted under its own reference (statement number), which the
data catalog's alias events resolve to a resource id. Register rows have no catalog alias, so their
resource is derived from the order number: across the evidence, ORD-2026<N> always belongs to
<resource>_<N> (e.g. legal_hold_4743 has order_reference ORD-20264743). Derived links are marked
`derived_from` so policy can weigh them.
"""

import re
from datetime import datetime

import pymupdf

from ..data import EvidenceFile
from .model import USER_REF, Observation

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+\d[\d ()-]{6,}\d")
_RESOURCE = re.compile(r"\b(?:support|audit_record|account|search|analytics|export|backup|data_flow|tombstone|legal_hold)_\d+\b")
_ORDER = re.compile(r"\bORD-2026(\d+)\b")
_DATE = re.compile(r"(\d{1,2} \w{3} \d{4})(?:\s+(\d{2})\s+(\d{2}) UTC)?")

# Title fragment -> (system_id, data_category, resource prefix for order-derived ids or None).
_KINDS = {
    "DELIVERY ADDRESS CORRECTION REQUEST": ("support_platform", "SUPPORT_ATTACHMENT", None),
    "COMPLIANCE JOURNAL": ("security_service", "SECURITY_AUDIT_LOG", None),
    "DISPUTED ORDERS AND TAX RECORDS REGISTER": ("legal_service", "LEGAL_HOLD_RECORD", "legal_hold"),
    "CUSTOMER STATEMENT": ("export_service", "USER_EXPORT", None),
}
# A document's own reference ("Statement NST-2026-0718-1842"); the data catalog maps it to a resource.
_DOC_REFERENCE = re.compile(r"\b(?:Statement|Reference)\s+([A-Z]{2,}-[\w-]+)")


def _iso(match: re.Match) -> str:
    day = datetime.strptime(match.group(1), "%d %b %Y")
    hour, minute = int(match.group(2) or 0), int(match.group(3) or 0)
    return day.replace(hour=hour, minute=minute).strftime("%Y-%m-%dT%H:%M:%SZ")


def _retain_until(text: str) -> str | None:
    m = re.search(r"retain until\s+" + _DATE.pattern, text, re.IGNORECASE)
    return _iso(m) if m else None


_LAYOUT_LABELS = {"Detail", "Record", "Item"}
_PERSON = re.compile(r"[A-Z][\w'-]+(?: [A-Z][\w'-]+)+")


def _name(lines: list[str]) -> str | None:
    """A person's name: 'Customer <Name>', or the name-like line just before an email line."""
    content = [line for line in lines if line not in _LAYOUT_LABELS]
    for i, line in enumerate(content):
        if m := re.match(r"Customer (" + _PERSON.pattern + r")$", line):
            return m.group(1)
        if _EMAIL.fullmatch(line) and i > 0 and _PERSON.fullmatch(content[i - 1]):
            return content[i - 1]
    return None


def _register_rows(ev: EvidenceFile, lines: list[str], system_id: str, category: str, prefix: str) -> list[Observation]:
    """ORD-2026N | user_NN | reason | date | amount | Retain until <date> | initials"""
    observations = []
    for line in lines:
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 6 or not (order := _ORDER.fullmatch(cells[0])):
            continue
        observations.append(
            Observation(
                file_id=ev.file_id,
                observed_at=ev.captured_at,
                system_id=system_id,
                resource_id=f"{prefix}_{order.group(1)}",
                exists=True,
                user_ref=cells[1] if USER_REF.fullmatch(cells[1]) else None,
                attrs={
                    "data_category": category,
                    "order_reference": cells[0],
                    "retention_reason": cells[2],
                    "retention_until": _retain_until(line),
                    "derived_from": "order_reference",
                },
            )
        )
    return observations


def parse(ev: EvidenceFile) -> list[Observation]:
    with pymupdf.open(ev.path) as doc:
        text = "\n".join(page.get_text() for page in doc)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    kind = next((k for k in _KINDS if any(k in line for line in lines[:2])), None)
    system_id, category, prefix = _KINDS.get(kind, (None, None, None))

    if kind and prefix and "|" in text:  # tabular register
        return _register_rows(ev, lines, system_id, category, prefix)

    explicit = sorted(set(_RESOURCE.findall(text)))
    orders = sorted(set(_ORDER.findall(text)))
    reference = _DOC_REFERENCE.search(text)
    if explicit:
        targets = [(rid, None) for rid in explicit]
    elif reference:  # resolved to a resource id through the data catalog's alias (see linking.py)
        targets = [(reference.group(1), None)]
    elif prefix and orders:
        targets = [(f"{prefix}_{n}", f"ORD-2026{n}") for n in orders]
    else:
        return []

    users = sorted(set(USER_REF.findall(text)))
    emails, phones = _EMAIL.findall(text), _PHONE.findall(text)
    observations = []
    for resource_id, order in targets:
        attrs = {"data_category": category, "document_kind": kind, "retention_until": _retain_until(text)}
        if order:
            attrs.update(order_reference=order, derived_from="order_reference")
        observations.append(
            Observation(
                file_id=ev.file_id,
                observed_at=ev.captured_at,
                system_id=system_id or resource_id.rsplit("_", 1)[0],
                resource_id=resource_id,
                exists=True,
                user_ref=users[0] if len(users) == 1 else None,
                email=emails[0] if emails else None,
                phone=phones[0] if phones else None,
                name=_name(lines),
                attrs={k: v for k, v in attrs.items() if v is not None},
            )
        )
    return observations
