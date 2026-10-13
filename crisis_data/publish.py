"""Publish gate. P1 only implements --dry-run; nothing leaves the machine.

Rule (spec 001): publish refuses any source whose `license:` in sources.yaml is
not `verified` while that source's rows would enter the published panel. A
source with `panel: false` contributes to coverage counts only, so it passes
the gate whatever its license.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import yaml

from crisis_data.paths import OUT, SOURCES_YAML


def check(sources_yaml: Path = SOURCES_YAML, out: Path = OUT) -> list[str]:
    cfg = yaml.safe_load(sources_yaml.read_text())
    panel_path = out / "panel_long.parquet"
    if not panel_path.exists():
        return [f"{panel_path} missing: run `crisis-data build` first"]
    present = set(pd.read_parquet(panel_path, columns=["source"]).source.unique())
    problems = []
    for name, s in sorted(cfg["sources"].items()):
        lic = s.get("license")
        in_panel = s.get("panel", True) and name in present
        if in_panel and lic != "verified":
            problems.append(f"{name}: license is '{lic}', not 'verified', and its rows are in the panel")
        if lic == "verified" and not (s.get("license_url") and s.get("license_text")):
            problems.append(f"{name}: license 'verified' needs license_url and license_text")
    return problems


def main(dry_run: bool, sources_yaml: Path = SOURCES_YAML, out: Path = OUT) -> int:
    problems = check(sources_yaml, out)
    for p in problems:
        print(f"publish gate: {p}", file=sys.stderr)
    if problems:
        print("publish refused", file=sys.stderr)
        return 1
    if dry_run:
        print("publish gate passed (dry run, nothing sent)")
        return 0
    print("publish: P2 (public repo + site) is not built yet; nothing sent", file=sys.stderr)
    return 2
