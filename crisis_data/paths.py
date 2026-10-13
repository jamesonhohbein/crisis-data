"""Repository paths. Data directories are never on sys.path and never imported from."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("CRISIS_DATA_ROOT", Path(__file__).resolve().parent.parent))
DATA = ROOT / "data"
RAW = DATA / "raw"
OUT = DATA / "out"
REF = ROOT / "ref"
SOURCES_YAML = ROOT / "sources.yaml"

COUNTRIES = {"SDN": "Sudan", "YEM": "Yemen"}
# Coverage and the panel start here (spec: month slider from Jan 2023).
START_MONTH = "2023-01"


def latest_snapshot(source: str, raw: Path | None = None) -> Path:
    """Newest data/raw/<source>/<YYYY-MM-DD>/ that has a manifest."""
    base = (raw or RAW) / source
    snaps = sorted(p for p in base.glob("????-??-??") if (p / "MANIFEST.sha256").exists())
    if not snaps:
        raise FileNotFoundError(f"no snapshot with manifest under {base}; run `crisis-data fetch`")
    return snaps[-1]
