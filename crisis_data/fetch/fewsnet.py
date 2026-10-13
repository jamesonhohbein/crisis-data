"""FEWS NET Data Warehouse (FDW) API, unauthenticated.

Observed API limits (2026-10-12): the WAF answers 403 for page_size >= 1000
and for offset >= 1000, so JSON paging cannot walk a full country. The CSV
export of a filtered query returns the whole result in one response. We
therefore partition by country x month (prices, status=Collected) and country
x year (exchange rates), take the CSV for each partition, and cross-check its
row count against the JSON `count` for the same filter. A mismatch fails the
fetch. One partition request per second.
"""

from __future__ import annotations

import calendar
import csv
import datetime as dt
import io
import time
from pathlib import Path

from crisis_data.fetch.common import get, now_iso, snapshot_dir, write_bytes, write_manifest

API = "https://fdw.fews.net/api"
COUNTRIES = {"SDN": "SD", "YEM": "YE"}
START = dt.date(2023, 1, 1)
PAUSE_S = 1.0


def _months(start: dt.date, end: dt.date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        last = calendar.monthrange(y, m)[1]
        yield dt.date(y, m, 1), dt.date(y, m, last)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _csv_rows(data: bytes) -> int:
    """Rows with a fact id. The CSV export adds synthetic "No Data" rows (no id) that the JSON
    count leaves out; seen in exchangeratevaluefacts YE 2023 (1100 CSV rows, 4 without id, count 1096)."""
    return sum(1 for r in csv.DictReader(io.StringIO(data.decode("utf-8-sig"))) if r.get("id"))


def _pull(snap: Path, meta: dict, endpoint: str, name: str, params: dict) -> None:
    done = snap / name
    if done.exists() and not (snap / "MANIFEST.sha256").exists():
        # Resuming an interrupted fetch of the same snapshot: files are written atomically and
        # only after their count check passed, so an existing file is complete.
        meta["files"][name] = {"csv_rows": _csv_rows(done.read_bytes()), "resumed": True}
        return
    count = get(f"{API}/{endpoint}/", params={**params, "format": "json", "page_size": 1}).json()["count"]
    time.sleep(PAUSE_S)
    r = get(f"{API}/{endpoint}/", params={**params, "format": "csv"})
    n = _csv_rows(r.content)
    if n != count:
        raise RuntimeError(f"{endpoint} {params}: CSV has {n} rows, JSON count {count}")
    write_bytes(snap / name, r.content)
    meta["files"][name] = {"url": r.url, "json_count": count, "csv_rows": n}
    time.sleep(PAUSE_S)


def fetch(date: str | None = None, today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    snap = snapshot_dir("fewsnet", date)
    meta = {"source": "fewsnet", "fetched_at": now_iso(), "start": START.isoformat(), "files": {},
            "note": "prices filtered server-side to status=Collected"}
    for iso3, cc in COUNTRIES.items():
        for a, b in _months(START, today):
            _pull(snap, meta, "marketpricefacts", f"marketpricefacts/{iso3.lower()}/{a:%Y-%m}.csv",
                  {"country_code": cc, "start_date": a.isoformat(), "end_date": b.isoformat(), "status": "Collected"})
        for y in range(START.year, today.year + 1):
            _pull(snap, meta, "exchangeratevaluefacts", f"exchangeratevaluefacts/{iso3.lower()}/{y}.csv",
                  {"country_code": cc, "start_date": f"{y}-01-01", "end_date": f"{y}-12-31"})
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "files": len(meta["files"])}


def iter_rows(snap: Path, endpoint: str, iso3: str):
    """Yield (file, line_number, row) from stored CSVs. Plain csv parsing only."""
    for p in sorted((snap / endpoint / iso3.lower()).glob("*.csv")):
        with open(p, newline="", encoding="utf-8-sig") as f:
            rd = csv.DictReader(f)
            for row in rd:
                yield p, rd.line_num, row
