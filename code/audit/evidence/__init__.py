"""Stage 3: parse every evidence file into normalized observations of resource state."""

import fnmatch
import sys
from collections.abc import Callable
from dataclasses import dataclass

from ..data import EvidenceFile
from . import documents, handover, images, json_records, lookup_trace, obslog, service_export, sql_snapshot
from .model import Coverage, Observation

Parser = Callable[[EvidenceFile], list[Observation | Coverage]]

# File-name pattern -> parser. Patterns match the path relative to dataset/evidence/; first match wins.
PARSERS: list[tuple[str, Parser]] = [
    ("records/service_resource_export_*.json", service_export.parse),
    ("logs/resource_lookup_trace_*.log", lookup_trace.parse),
    ("records/customer_account_handover_*.txt", handover.parse),
    ("logs/*.log", obslog.parse),
    ("databases/*.sql", sql_snapshot.parse),
    ("records/*.json", json_records.parse),  # after service_resource_export, which needs its own parser
    ("documents/*.pdf", documents.parse),
    ("images/*.png", images.parse),  # vision-model transcription, cached under code/.cache/vision
]


@dataclass
class Evidence:
    observations: list[Observation]
    coverage: list[Coverage]
    errors: list[str]  # files that could not be parsed (reported, not fatal)


def parser_for(ev: EvidenceFile) -> Parser | None:
    relative = ev.path.as_posix().split("/evidence/", 1)[-1]
    for pattern, parse in PARSERS:
        if fnmatch.fnmatch(relative, pattern):
            return parse
    return None


def load_evidence(files: list[EvidenceFile], verbose: bool = False) -> Evidence:
    """Parse every file. A file that cannot be parsed is reported and skipped, never fatal: one
    corrupt export must not stop the audit of every other request."""
    evidence, unparsed = Evidence([], [], []), []
    for ev in files:
        parse = parser_for(ev)
        if parse is None:
            unparsed.append(ev.file_id)
            continue
        try:
            items = list(parse(ev))
        except Exception as e:  # malformed or truncated evidence
            evidence.errors.append(f"{ev.file_id} ({ev.path.name}): {type(e).__name__}: {e}")
            print(f"[evidence] skipped {ev.file_id}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        for item in items:
            (evidence.coverage if isinstance(item, Coverage) else evidence.observations).append(item)
    if verbose and unparsed:
        print(f"[evidence] no parser yet for {len(unparsed)} files: {', '.join(unparsed)}", file=sys.stderr)
    return evidence


def load_observations(files: list[EvidenceFile], verbose: bool = False) -> list[Observation]:
    return load_evidence(files, verbose).observations


__all__ = ["Coverage", "Evidence", "Observation", "load_evidence", "load_observations", "parser_for"]
