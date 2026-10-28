"""Stage 1: decide whether request_text authorizes deletion (any language; Gemini-backed).

Unique request texts are classified in batches with one structured prompt, cached on disk, so a
full run costs a handful of calls and reruns cost none. The model reports a request type and an
explicit authorizes_deletion flag; NOT_APPLICABLE follows from that flag alone.
"""

from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from .data import Request
from .llm import generate_json

BATCH_SIZE = 100
# Classification runs on its own model (free-tier quotas are per model per day; vision used the default).
MODEL = "gemini-3.5-flash-lite"

RequestType = Literal[
    "deletion",            # delete/erase/close the account or remove the person's data (fully or specific copies)
    "access_or_export",    # wants a copy, export, download or invoices
    "correction",          # fix or change details (name, email, address)
    "marketing_opt_out",   # stop marketing only, keep the account
    "account_support",     # login, password, technical help
    "other",
]


class Verdict(BaseModel):
    id: str
    language: str = Field(description="ISO 639-1 code of the request's language, e.g. en, es, ja.")
    request_type: RequestType
    authorizes_deletion: bool


class Verdicts(BaseModel):
    verdicts: list[Verdict]


PROMPT = """You review privacy requests sent to an online shop's privacy team.
For each request below decide whether the person authorizes deletion of their personal data.

authorizes_deletion is TRUE when the person asks to delete, erase or close their account, or to remove
their personal data, in any language. This includes follow-ups on an earlier deletion request: asking
to finish it, or to check, verify, audit or be told whether their account or data has actually been
removed (for example because a status page says the deletion failed). It also includes requests to
delete specific remaining copies (a search result, export file, support attachment, analytics link,
backup or integration that could restore data) as part of deleting their data.
Allowing some records to be kept (anonymous statistics, security or legal records) does not change this.

authorizes_deletion is FALSE when the request only asks for something else: access to or a copy/export of
their data or invoices, correcting or changing details, login or account help, or unsubscribing from
marketing while keeping the account. It is also FALSE when the person says not to delete their data.

Judge only what the person asks for; ignore politeness and sign-off phrases.
Return one verdict per request, using the request's id.

Requests:
"""


def _prompt(batch: list[tuple[str, str]]) -> str:
    return PROMPT + "\n".join(f"[{rid}] {text}" for rid, text in batch)


def _classify_batch(batch: list[tuple[str, str]]) -> dict[str, Verdict]:
    answer = Verdicts.model_validate(
        generate_json("intent_classification", _prompt(batch), Verdicts, namespace="intent", model=MODEL)
    )
    by_id = {v.id: v for v in answer.verdicts}
    missing = [rid for rid, _ in batch if rid not in by_id]
    if missing:
        raise RuntimeError(f"intent classifier returned no verdict for {missing}")
    return {text: by_id[rid] for rid, text in batch}


def classify_texts(texts: list[str], workers: int = 1) -> dict[str, Verdict]:
    """Classify each distinct text once (batches run in parallel); returns text -> verdict."""
    unique = sorted(set(texts))
    batches = [
        [(f"r{start + i}", text) for i, text in enumerate(unique[start:start + BATCH_SIZE])]
        for start in range(0, len(unique), BATCH_SIZE)
    ]
    results: dict[str, Verdict] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for part in pool.map(_classify_batch, batches):
            results.update(part)
    return results


class IntentClassifier:
    def __init__(self, requests: list[Request]):
        self.verdicts = classify_texts([r.request_text for r in requests])

    def verdict(self, request: Request) -> Verdict:
        if request.request_text not in self.verdicts:  # a request outside the pre-classified set
            self.verdicts.update(classify_texts([request.request_text]))
        return self.verdicts[request.request_text]

    def authorizes_deletion(self, request: Request) -> bool:
        return self.verdict(request).authorizes_deletion
