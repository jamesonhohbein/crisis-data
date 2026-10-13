from __future__ import annotations

import json

import pandas as pd
import pytest

from crisis_data.paths import OUT, RAW


def _have_snapshots() -> bool:
    return all(any((RAW / s).glob("????-??-??/MANIFEST.sha256")) for s in ("wfp", "fewsnet", "codab"))


needs_raw = pytest.mark.skipif(not _have_snapshots(), reason="no raw snapshot; run `crisis-data fetch`")


@pytest.fixture(scope="session")
def built():
    """Build once per session from the latest raw snapshots into data/out."""
    if not _have_snapshots():
        pytest.skip("no raw snapshot; run `crisis-data fetch`")
    from crisis_data.build.panel import build

    build(out=OUT)
    return {
        "panel": pd.read_parquet(OUT / "panel_long.parquet"),
        "merged": pd.read_parquet(OUT / "panel_merged.parquet"),
        "coverage": pd.read_parquet(OUT / "coverage.parquet"),
        "report": json.loads((OUT / "build_report.json").read_text()),
    }
