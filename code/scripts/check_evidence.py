"""Smoke test: every file in evidence_files.csv exists and opens with the parser its format needs."""

import json
import sqlite3
import sys
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit.data import load_evidence_manifest  # noqa: E402


def check(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".sql":
        db = sqlite3.connect(":memory:")
        db.executescript(path.read_text(encoding="utf-8"))
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        rows = sum(db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables)
        return f"{len(tables)} tables, {rows} rows"
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return f"json {type(data).__name__} with {len(data)} keys/items"
    if suffix in (".log", ".txt"):
        return f"{len(path.read_text(encoding='utf-8').splitlines())} lines"
    if suffix == ".pdf":
        with pymupdf.open(path) as doc:
            pages, chars = len(doc), sum(len(page.get_text()) for page in doc)
        return f"{pages} pages, {chars} text chars"
    if suffix == ".png":
        with pymupdf.open(path) as img:
            rect = img[0].rect
        return f"image {int(rect.width)}x{int(rect.height)}"
    raise ValueError(f"unhandled format {suffix}")


def main() -> int:
    failures = 0
    for ev in load_evidence_manifest():
        try:
            print(f"ok    {ev.file_id}  {ev.path.relative_to(ev.path.parents[2])}  {check(ev.path)}")
        except Exception as e:  # report every failure, not just the first
            failures += 1
            print(f"FAIL  {ev.file_id}  {ev.path}  {type(e).__name__}: {e}")
    print(f"\n{failures} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
