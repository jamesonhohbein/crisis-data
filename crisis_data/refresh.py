"""Weekly refresh (spec 001 P3): fetch, build, publish, and the freshness signal the incident path reads.

`crisis-data refresh` is what `systemd/crisis-data.service` runs. Order and failure rules:

1. Fetch every source. A source that raises is recorded and the others still run, so one dead
   source does not hide whether the rest are alive. If any failed, metrics are written and the
   run exits 1 before build: nothing is published from a run with a failed step.
2. Each fetched snapshot is settled against the previous one. If its data files are byte-identical
   to the previous snapshot's, the new directory is removed and the previous snapshot stays the
   latest; otherwise identical files are hardlinked to the previous copy. So an unchanged week
   builds from exactly the same inputs, the bundle is byte-identical (T8), and `publish` finds no
   diff and makes no gh-pages commit. It also keeps a weekly raw history at the size of what
   actually changed (about 130 MB per full snapshot otherwise).
3. Build, then publish. Any exception or non-zero exit fails the run. `publish` only pushes after
   the gate and the bundle audit pass, and the push is one commit, so a failure never leaves a
   partial bundle on gh-pages.

Freshness, per price source, is measured on the raw snapshot right after its fetch, independent of
build, so a failing source goes stale on its own without dragging the healthy ones with it:
- wfp, fewsnet: data rows across the snapshot's CSV files.
- jmmi: workbook count (one XLSX per published round).
A source has "new rows" when that measure rose since the last good fetch. A source that fails also
gets no new rows, so the one rule `crisis-data-source-stale` (no new rows for 35 days, in
~/Software/claude-telemetry/alerting/97-crisis-data.yaml) covers both "fails" and "returns nothing
new" for 5 weeks. `crisis-data-refresh-stale` covers the whole run not succeeding for 8 days, and
the generic `Systemd unit failed` rule covers a failed run as it happens.

State lives in data/state/freshness.json (gitignored) so timestamps survive failed runs; metrics
are rewritten from it on every run, success or not, into the node-exporter textfile directory.
"""

from __future__ import annotations

import csv
import datetime as dt
import importlib
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable

from crisis_data.fetch.common import sha256
from crisis_data.paths import DATA, RAW

FETCHERS = ("wfp", "fewsnet", "jmmi", "codab")
PRICE_SOURCES = ("wfp", "fewsnet", "jmmi")
STATE = DATA / "state" / "freshness.json"
METRICS = Path(os.environ.get("CRISIS_DATA_METRICS", "/home/jameson/.cache/borg/metrics/crisis_data.prom"))
# Fetch bookkeeping, not source data: these change on every fetch (fetch time, HDX page views,
# listing crawl progress) and must not make an unchanged snapshot look new.
META_FILES = {"fetch_meta.json", "MANIFEST.sha256"}
META_PREFIXES = ("hdx_package_", "listing_")


# ---------------------------------------------------------------- snapshots
def _manifest(snap: Path) -> dict[str, str]:
    out = {}
    for line in (snap / "MANIFEST.sha256").read_text().splitlines():
        if line.strip():
            digest, name = line.split("  ", 1)
            out[name] = digest
    return out


def _is_data(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    return base not in META_FILES and not base.startswith(META_PREFIXES)


def previous_snapshot(snap: Path) -> Path | None:
    """Newest sibling snapshot with a manifest, dated before `snap`."""
    older = sorted(p for p in snap.parent.glob("????-??-??")
                   if p.name < snap.name and (p / "MANIFEST.sha256").exists())
    return older[-1] if older else None


def settle(snap: Path) -> str:
    """Drop `snap` if its data equals the previous snapshot's, else hardlink unchanged files.

    Returns "first", "unchanged" (snap removed) or "changed:<n linked>"."""
    prev = previous_snapshot(snap)
    if prev is None:
        return "first"
    new, old = _manifest(snap), _manifest(prev)
    new_d = {k: v for k, v in new.items() if _is_data(k)}
    old_d = {k: v for k, v in old.items() if _is_data(k)}
    if new_d == old_d:
        shutil.rmtree(snap)
        return "unchanged"
    linked = 0
    for name, digest in new_d.items():
        if old_d.get(name) != digest:
            continue
        src, dst = prev / name, snap / name
        if src.stat().st_ino == dst.stat().st_ino or sha256(src) != digest:
            continue
        tmp = dst.with_name(dst.name + ".link")
        os.link(src, tmp)
        tmp.replace(dst)
        linked += 1
    return f"changed:{linked}"


def latest(source: str, raw: Path) -> Path | None:
    snaps = sorted(p for p in (raw / source).glob("????-??-??") if (p / "MANIFEST.sha256").exists())
    return snaps[-1] if snaps else None


def measure(source: str, snap: Path) -> int:
    """Rows for wfp/fewsnet (CSV data rows), rounds for jmmi (XLSX files). Plain csv parsing only."""
    if source == "jmmi":
        return sum(1 for _ in snap.rglob("*.xlsx"))
    n = 0
    for p in sorted(snap.rglob("*.csv")):
        with open(p, newline="", encoding="utf-8-sig") as f:
            n += max(sum(1 for _ in csv.reader(f)) - 1, 0)
    return n


# ---------------------------------------------------------------- state + metrics
def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"sources": {}, "run_last_ok": None, "run_last_attempt": None}


def save_state(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def record_fetch(state: dict, source: str, snap: Path | None, now: float) -> None:
    s = state["sources"].setdefault(source, {})
    s["fetch_last_ok"] = now
    if source not in PRICE_SOURCES or snap is None:
        return
    m = measure(source, snap)
    prev = s.get("rows")
    if prev is None or m > prev:
        s["last_new_rows"] = now
    s["rows"] = m
    s["snapshot"] = snap.name


def render_metrics(state: dict) -> str:
    out = []

    def gauge(name: str, help_: str, samples: list[tuple[str, float]]) -> None:
        if not samples:
            return
        out.append(f"# HELP {name} {help_}")
        out.append(f"# TYPE {name} gauge")
        out.extend(f"{name}{lbl} {int(v)}" for lbl, v in samples)

    src = sorted(state["sources"].items())
    gauge("crisis_data_run_last_ok_timestamp_seconds",
          "End of the last refresh run where fetch, build and publish all succeeded.",
          [("", state["run_last_ok"])] if state.get("run_last_ok") else [])
    gauge("crisis_data_run_last_attempt_timestamp_seconds", "Start of the last refresh run.",
          [("", state["run_last_attempt"])] if state.get("run_last_attempt") else [])
    gauge("crisis_data_source_fetch_last_ok_timestamp_seconds", "Last successful fetch of the source.",
          [(f'{{source="{k}"}}', v["fetch_last_ok"]) for k, v in src if v.get("fetch_last_ok")])
    gauge("crisis_data_source_last_new_rows_timestamp_seconds",
          "Last fetch where the source's row count (jmmi: rounds) rose.",
          [(f'{{source="{k}"}}', v["last_new_rows"]) for k, v in src if v.get("last_new_rows")])
    gauge("crisis_data_source_rows", "Rows in the source's latest raw snapshot (jmmi: rounds).",
          [(f'{{source="{k}"}}', v["rows"]) for k, v in src if v.get("rows") is not None])
    return "\n".join(out) + "\n"


def write_metrics(state: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".crisis_data.", suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        f.write(render_metrics(state))
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


# ---------------------------------------------------------------- run
def _default_fetch(name: str) -> Callable[[str], dict]:
    return importlib.import_module(f"crisis_data.fetch.{name}").fetch


def _default_build() -> None:
    from crisis_data.build.panel import build

    build()


def _default_publish() -> int:
    from crisis_data.publish import main

    return main(dry_run=False)


def run(today: str | None = None, raw: Path = RAW, state_path: Path = STATE, metrics_path: Path = METRICS,
        fetch: Callable[[str], Callable[[str], dict]] = _default_fetch, build: Callable[[], None] = _default_build,
        publish: Callable[[], int] = _default_publish, clock: Callable[[], float] = time.time) -> int:
    today = today or dt.date.today().isoformat()
    state = load_state(state_path)
    state["run_last_attempt"] = clock()
    failed = []
    try:
        for name in FETCHERS:
            try:
                fetch(name)(today)
                snap = raw / name / today
                verdict = settle(snap) if (snap / "MANIFEST.sha256").exists() else "missing"
                if verdict == "missing":
                    raise RuntimeError(f"no manifest in {snap} after fetch")
                cur = latest(name, raw)
                record_fetch(state, name, cur, clock())
                print(f"{name}: {verdict}, latest {cur.name if cur else None}, "
                      f"rows {state['sources'][name].get('rows')}", flush=True)
            except Exception as e:  # keep going so every source reports
                print(f"{name}: FAILED {e}", file=sys.stderr, flush=True)
                failed.append(name)
        if failed:
            print(f"refresh: fetch failed for {failed}; not building or publishing", file=sys.stderr)
            return 1
        build()
        rc = publish()
        if rc != 0:
            print(f"refresh: publish exited {rc}", file=sys.stderr)
            return rc
        state["run_last_ok"] = clock()
        print("refresh: ok", flush=True)
        return 0
    finally:
        save_state(state, state_path)
        write_metrics(state, metrics_path)
