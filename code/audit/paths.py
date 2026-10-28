"""Repository-relative paths, so the agent runs from any checkout location."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET_DIR = REPO_ROOT / "dataset"
EVIDENCE_DIR = DATASET_DIR / "evidence"
OUTPUT_PATH = REPO_ROOT / "output.csv"
CACHE_DIR = REPO_ROOT / "code" / ".cache"
