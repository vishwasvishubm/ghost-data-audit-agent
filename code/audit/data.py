"""Loaders for the participant-facing CSV files in dataset/."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .paths import DATASET_DIR, EVIDENCE_DIR


def read_csv(name: str) -> list[dict[str, str]]:
    with open(DATASET_DIR / name, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def case_sort_key(case_id: str) -> tuple[int, str]:
    """Natural order: case_33 < case_100, matching deletion_requests.csv."""
    m = re.search(r"(\d+)$", case_id)
    return (int(m.group(1)) if m else 0, case_id)


@dataclass(frozen=True)
class Request:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass(frozen=True)
class EvidenceFile:
    file_id: str
    path: Path
    origin: str
    captured_at: str


def load_requests(name: str = "deletion_requests.csv") -> list[Request]:
    return [Request(**row) for row in read_csv(name)]


def load_evidence_manifest() -> list[EvidenceFile]:
    return [
        EvidenceFile(
            file_id=row["file_id"],
            path=DATASET_DIR / row["file_path"],
            origin=row["origin"],
            captured_at=row["captured_at"],
        )
        for row in read_csv("evidence_files.csv")
    ]


@dataclass
class Dataset:
    """Everything the agent may consult besides the request itself."""

    identities: list[dict[str, str]]
    audit_log: list[dict[str, str]]
    activity: list[dict[str, str]]
    rules: list[dict[str, str]]
    evidence: list[EvidenceFile]


def load_dataset() -> Dataset:
    return Dataset(
        identities=read_csv("user_identities.csv"),
        audit_log=read_csv("user_audit_log.csv"),
        activity=read_csv("system_activity.csv"),
        rules=read_csv("deletion_rules.csv"),
        evidence=load_evidence_manifest(),
    )


__all__ = [
    "EVIDENCE_DIR",
    "Dataset",
    "EvidenceFile",
    "Request",
    "case_sort_key",
    "load_dataset",
    "load_evidence_manifest",
    "load_requests",
    "read_csv",
]
