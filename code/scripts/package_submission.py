"""Build code.zip for submission from git-tracked files only.

Includes README.md, .env.example and everything tracked under code/ (agent, evaluation reports,
model-answer cache). Tracked files cannot include .venv, __pycache__, .env or dataset/; the script
still refuses nested archives and anything that looks like an API key.

    python code/scripts/package_submission.py
"""

import re
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INCLUDE = ["README.md", ".env.example", "code"]
FORBIDDEN_SUFFIXES = (".zip", ".tar", ".gz", ".tgz", ".7z", ".rar")
KEY_PATTERN = re.compile(rb"AIza[0-9A-Za-z_-]{30,}")  # Google API key shape


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(REPO_ROOT / "code.zip"), help="archive path (default: code.zip)")
    args = parser.parse_args()
    tracked = subprocess.run(["git", "ls-files", "--", *INCLUDE], capture_output=True, text=True,
                             cwd=REPO_ROOT, check=True).stdout.split()
    problems = []
    for path in tracked:
        if path.lower().endswith(FORBIDDEN_SUFFIXES):
            problems.append(f"nested archive: {path}")
        if any(part in ("__pycache__", ".venv", "venv", "node_modules", "dataset") for part in Path(path).parts):
            problems.append(f"excluded directory: {path}")
        if Path(path).name == ".env" or KEY_PATTERN.search((REPO_ROOT / path).read_bytes()):
            problems.append(f"possible secret: {path}")
    if problems:
        print("\n".join(problems))
        return 1

    out = Path(args.out)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(tracked):
            zf.write(REPO_ROOT / path, arcname=path)
    print(f"wrote {out} ({len(tracked)} files, {out.stat().st_size / 1024:.0f} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
