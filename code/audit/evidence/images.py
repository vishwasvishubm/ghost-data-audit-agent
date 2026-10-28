"""Parser for images/*.png: internal product screenshots, read with a vision model.

The model only transcribes the screen into a fixed schema (header, title, cards, table, detail
panel, activity); it makes no judgements. Turning the transcription into observations is
deterministic code below, using the same identifier rules as the other parsers:

  * ids in the page title/subtitle are the page's subject and carry its name/email/phone;
  * other ids on a subject page are linked to the subject but not shown exposing it;
  * ids in table rows carry only that row's identifiers (list screens mix customers and decoys).
"""

import re

from pydantic import BaseModel, Field

from ..data import EvidenceFile
from ..llm import generate_json
from .model import USER_REF, Observation

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\+\d[\d ()-]{6,}\d")
_PERSON = re.compile(r"[A-Z][\w'-]+(?: [A-Z][\w'-]+)+")
_FILE = re.compile(r"[\w.-]+\.(?:pdf|jpe?g|png|zip|csv)", re.IGNORECASE)
_RESOURCE = re.compile(r"\b(?:account|search|analytics|support|export|backup|data_flow|audit_record|tombstone|legal_hold|marketing)_\d+\b")

# Resource-id prefix -> (system_id, data_category).
_PREFIXES = {
    "account": ("account_service", "USER_PROFILE"),
    "search": ("search_service", "SEARCH_INDEX_ENTRY"),
    "analytics": ("analytics_platform", "ANALYTICS_PROFILE"),
    "support": ("support_platform", "SUPPORT_MESSAGE"),
    "export": ("export_service", "USER_EXPORT"),
    "backup": ("backup_service", "BACKUP_ARCHIVE"),
    "data_flow": ("integration_service", None),
    "audit_record": ("security_service", "SECURITY_AUDIT_LOG"),
    "tombstone": ("identity_service", "ACCOUNT_TOMBSTONE"),
    "legal_hold": ("legal_service", "LEGAL_HOLD_RECORD"),
    "marketing": ("marketing_platform", "MARKETING_PROFILE"),
}
# Systems where an email names the subject a process acts on (selector/account), not exposed data.
_LINK_ONLY_SYSTEMS = {"integration_service", "backup_service"}
_NAME_LABELS = {"customer", "name", "display name", "display_name", "requester"}


class Pair(BaseModel):
    label: str
    value: str


class Row(BaseModel):
    cells: list[Pair] = Field(description="One entry per column: label is the column header, value the cell text.")


class Event(BaseModel):
    time: str
    text: str


class Screen(BaseModel):
    application: str = Field(description="Product name shown in the top header bar, e.g. 'Search Console'.")
    breadcrumb: str
    page_title: str
    page_subtitle: str = Field(description="The line directly under the page title.")
    status_badge: str
    search_query: str = Field(description="Text in the top search box if it holds a query, else empty.")
    summary_cards: list[Pair]
    table_title: str
    table_rows: list[Row]
    detail_title: str = Field(description="Heading of the right-hand detail panel.")
    detail_fields: list[Pair]
    activity: list[Event]


PROMPT = """You are transcribing a screenshot of an internal business application for an audit.
Transcribe exactly what is visible into the schema. Do not interpret, summarise, infer or correct.
- Copy identifiers (ids like account_123, emails, phone numbers, user_12, ORD-..., CS-...) character for character, preserving case.
- Include every table row, every summary card, every detail-panel field and every activity item.
- For table rows, give one cell per column using the column header as the label.
- Use an empty string for anything not shown; write a dash cell (—) as an empty string.
"""


def _ids(text: str) -> list[str]:
    return list(dict.fromkeys(_RESOURCE.findall(text)))


def _identity(pairs: list[tuple[str, str]], system_id: str) -> dict:
    """Identifier kwargs from (label, value) pairs."""
    ids: dict = {}
    for label, value in pairs:
        if not value:
            continue
        if USER_REF.fullmatch(value) and "user_ref" not in ids:
            ids["user_ref"] = value
        elif (m := _EMAIL.search(value)) and "email" not in ids and "lookup_key" not in ids:
            link_only = system_id in _LINK_ONLY_SYSTEMS or label.lower() in ("selector", "search")
            ids["lookup_key" if link_only else "email"] = m.group(0)
        elif (m := _PHONE.search(value)) and "phone" not in ids:
            ids["phone"] = m.group(0)
        elif label.lower() in _NAME_LABELS and _PERSON.fullmatch(value) and "name" not in ids:
            ids["name"] = value
    return ids


def _state(system_id: str, pairs: list[tuple[str, str]]) -> dict:
    values = {label.lower(): value for label, value in pairs}
    attrs: dict = {}
    status = (values.get("status") or "").lower()
    if system_id == "integration_service":
        mode = (values.get("mode") or values.get("sync mode") or "").lower()
        if status in ("healthy", "active", "running") or mode.startswith("continuous"):
            attrs["enabled"] = True
        if status in ("paused", "disabled", "stopped"):
            attrs["enabled"] = False
    if system_id == "export_service" and status:
        attrs["download_available"] = status == "ready"
    for label, value in values.items():
        if "restore" in label and value:
            attrs["restore_allowed"] = value.lower() in ("allowed", "enabled", "yes", "true")
    return attrs


def screen_observations(ev: EvidenceFile, screen: dict) -> list[Observation]:
    entities: dict[str, dict] = {}

    def add(resource_id: str, pairs: list[tuple[str, str]], linked_only: bool = False) -> None:
        system_id, category = _PREFIXES[resource_id.rsplit("_", 1)[0]]
        ids = _identity(pairs, system_id)
        if linked_only:  # mentioned on someone's page: a link to them, not proof of exposure
            ids = {"user_ref": ids.get("user_ref"), "lookup_key": ids.get("email") or ids.get("lookup_key")}
        entity = entities.setdefault(resource_id, {"system_id": system_id, "category": category, "ids": {}, "pairs": []})
        for k, v in ids.items():
            if v:
                entity["ids"].setdefault(k, v)
        entity["pairs"] += pairs

    detail = [(f["label"], f["value"]) for f in screen["detail_fields"]]
    cards = [(c["label"], c["value"]) for c in screen["summary_cards"]]
    heading = f"{screen['page_title']} · {screen['page_subtitle']}"
    title_name = [("name", screen["page_title"])] if _PERSON.fullmatch(screen["page_title"]) else []
    page_pairs = title_name + [("", part.strip()) for part in screen["page_subtitle"].split("·")] + cards
    subjects = _ids(heading)

    for resource_id in subjects:
        add(resource_id, page_pairs + (detail if not _ids(screen["detail_title"]) else []))

    for row in screen["table_rows"]:
        pairs = [(c["label"], c["value"]) for c in row["cells"]]
        for resource_id in _ids(" ".join(v for _, v in pairs)):
            add(resource_id, pairs)

    for resource_id in _ids(screen["detail_title"]):
        add(resource_id, detail)

    linked_text = " ".join(v for _, v in detail + cards) + " " + " ".join(a["text"] for a in screen["activity"])
    for resource_id in _ids(linked_text):
        if resource_id not in entities and subjects:
            add(resource_id, page_pairs, linked_only=True)

    return [
        Observation(
            file_id=ev.file_id,
            observed_at=ev.captured_at,
            system_id=e["system_id"],
            resource_id=resource_id,
            exists=True,
            attrs={"data_category": e["category"], "screen": screen["application"], **_state(e["system_id"], e["pairs"])},
            **e["ids"],
        )
        for resource_id, e in entities.items()
    ] + _alias_observations(ev, screen, subjects)


def _alias_observations(ev: EvidenceFile, screen: dict, subjects: list[str]) -> list[Observation]:
    """Things a screen shows only by business name: stored files, and pages about a bare user_NN.

    They are emitted under that name; the data catalog's alias events map names to resource ids
    (see linking.py), and names that never resolve are ignored downstream.
    """
    observations = []
    attachments = "attachment" in f"{screen['page_subtitle']} {screen['breadcrumb']}".lower()
    for row in screen["table_rows"]:
        values = [c["value"] for c in row["cells"]]
        files = [v for v in values if _FILE.fullmatch(v)]
        if files and not _ids(" ".join(values)):
            observations.append(Observation(
                file_id=ev.file_id, observed_at=ev.captured_at, system_id="object_storage", resource_id=files[0],
                exists=True, attrs={"file_name": files[0], "data_category": "SUPPORT_ATTACHMENT" if attachments else None,
                                    "screen": screen["application"]},
            ))
    title = screen["page_title"].strip()
    if not subjects and USER_REF.fullmatch(title):
        observations.append(Observation(
            file_id=ev.file_id, observed_at=ev.captured_at, system_id="unknown", resource_id=title, exists=True,
            user_ref=title, attrs={"status": screen["status_badge"], "screen": screen["application"]},
        ))
    return observations


def transcribe(ev: EvidenceFile) -> dict:
    return generate_json(
        purpose="screenshot_transcription",
        prompt=PROMPT,
        schema=Screen,
        images=[(ev.path.read_bytes(), "image/png")],
        namespace="vision",
    )


def parse(ev: EvidenceFile) -> list[Observation]:
    return screen_observations(ev, transcribe(ev))
