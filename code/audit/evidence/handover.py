"""Parser for records/customer_account_handover_*.txt: shift-handover checklist lines.

    - <time> | <system_id> | <resource_id> | <DATA_CATEGORY> | <STATUS> | <identifier>

The identifier is an email (any case), a stable user_NN reference, or "no direct identifier".
"""

import re

from ..data import EvidenceFile
from .model import Observation, status_observation

_LINE = re.compile(r"^-\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*(.*?)\s*$")


def parse(ev: EvidenceFile) -> list[Observation]:
    observations = []
    for line in ev.path.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        ts, system_id, resource_id, category, status, identifier = m.groups()
        observations.append(
            Observation(
                file_id=ev.file_id,
                observed_at=ts,
                system_id=system_id,
                resource_id=resource_id,
                **status_observation(status, system_id, category, identifier),
            )
        )
    return observations
