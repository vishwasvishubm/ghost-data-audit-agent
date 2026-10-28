"""Gemini client: deterministic structured-JSON calls with an on-disk cache and a usage ledger.

Every call is cached under code/.cache/<namespace>/ by a hash of (model, prompt, schema, inputs),
so reruns are free, reproducible and work without an API key once the cache is populated. The
ledger records every call (live or cached) for evaluation/usage_report.md.
"""

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass

from dotenv import load_dotenv
from pydantic import BaseModel

from .paths import CACHE_DIR, REPO_ROOT

load_dotenv(REPO_ROOT / ".env")

PROVIDER = "google"
DEFAULT_MODEL = "gemini-3.8-flash"
# Transcription and classification need little reasoning; low thinking cuts latency and output tokens.
THINKING_LEVEL = "low"


def _thinking(model: str) -> dict:
    """Gemini 2.5 models take a token budget (0 = off); Gemini 3 models take a level."""
    return {"thinking_budget": 0} if model.startswith("gemini-2.5") else {"thinking_level": THINKING_LEVEL}


def model_name(default: str = DEFAULT_MODEL) -> str:
    return os.getenv("GEMINI_MODEL") or default


@dataclass
class Call:
    purpose: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int  # includes thinking tokens, which are billed as output
    cached: bool


class UsageLedger:
    def __init__(self) -> None:
        self.calls: list[Call] = []

    def record(self, call: Call) -> None:
        self.calls.append(call)

    def summary(self) -> list[dict]:
        return [asdict(c) for c in self.calls]


LEDGER = UsageLedger()


def _client():
    from google import genai  # imported lazily so cached runs need no SDK setup or key

    key = os.getenv("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set; add it to .env (see .env.example) or populate the cache.")
    return genai.Client(api_key=key)


def _cache_key(model: str, prompt: str, schema: type[BaseModel], inputs: list[bytes]) -> str:
    h = hashlib.sha256()
    for part in (model, json.dumps(_thinking(model)) if not model.startswith("gemini-3") else THINKING_LEVEL,
                 prompt, json.dumps(schema.model_json_schema(), sort_keys=True)):
        h.update(part.encode("utf-8"))
    for data in inputs:
        h.update(hashlib.sha256(data).digest())
    return h.hexdigest()[:32]


def generate_json(
    purpose: str,
    prompt: str,
    schema: type[BaseModel],
    images: list[tuple[bytes, str]] = (),
    namespace: str = "llm",
    model: str | None = None,
) -> dict:
    """Return the model's JSON answer (validated against `schema`) for a prompt plus optional images."""
    model = model or model_name()
    key = _cache_key(model, prompt, schema, [data for data, _ in images])
    path = CACHE_DIR / namespace / f"{key}.json"
    if path.exists():
        entry = json.loads(path.read_text(encoding="utf-8"))
        LEDGER.record(Call(purpose, PROVIDER, model, entry["usage"]["input_tokens"], entry["usage"]["output_tokens"], cached=True))
        return entry["response"]

    from google.genai import types

    client = _client()
    parts = [types.Part.from_bytes(data=data, mime_type=mime) for data, mime in images] + [prompt]
    config = types.GenerateContentConfig(
        temperature=0.0,
        seed=7,
        response_mime_type="application/json",
        response_schema=schema,
        thinking_config=types.ThinkingConfig(**_thinking(model)),
    )
    backoff = (15, 30, 60, 90)  # free-tier per-minute limits and "high demand" spells need real waits
    for attempt in range(len(backoff) + 1):
        try:
            response = client.models.generate_content(model=model, contents=parts, config=config)
            break
        except Exception as e:  # rate limits and transient server errors: back off and retry
            if "PerDay" in str(e):  # a daily quota will not recover within any sensible backoff
                raise RuntimeError(f"daily Gemini quota exhausted for {model}; try another model or later") from e
            transient = any(code in str(e) for code in ("429", "500", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))
            if attempt == len(backoff) or not transient:
                raise
            time.sleep(backoff[attempt])

    answer = schema.model_validate_json(response.text).model_dump()
    meta = response.usage_metadata
    usage = {
        "input_tokens": meta.prompt_token_count or 0,
        "output_tokens": (meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "purpose": purpose, "usage": usage, "response": answer}, indent=1, ensure_ascii=False), encoding="utf-8")
    LEDGER.record(Call(purpose, PROVIDER, model, usage["input_tokens"], usage["output_tokens"], cached=False))
    return answer
