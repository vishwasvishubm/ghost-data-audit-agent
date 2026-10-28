"""Transcribe every screenshot in parallel to populate code/.cache/vision (needs GEMINI_API_KEY).

The main pipeline reads screenshots through the same cache, so after this runs it needs no key.

    python code/scripts/prefetch_vision.py [--workers 4]
"""

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import load_evidence_manifest  # noqa: E402
from audit.evidence import images  # noqa: E402
from audit.llm import LEDGER  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    shots = [ev for ev in load_evidence_manifest() if ev.path.suffix.lower() == ".png"]
    start, failures = time.time(), 0
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(images.transcribe, ev): ev for ev in shots}
        for future in as_completed(futures):
            ev = futures[future]
            try:
                screen = future.result()
                print(f"ok    {ev.file_id}  {ev.path.name}  rows={len(screen['table_rows'])}  [{time.time() - start:.0f}s]")
            except Exception as e:
                failures += 1
                print(f"FAIL  {ev.file_id}  {ev.path.name}  {type(e).__name__}: {e}")

    live = [c for c in LEDGER.calls if not c.cached]
    print(f"\n{len(shots)} screenshots, {len(live)} live calls, "
          f"{sum(c.input_tokens for c in live)} input / {sum(c.output_tokens for c in live)} output tokens, {failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
