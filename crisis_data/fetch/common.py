"""Shared fetch helpers: polite HTTP, snapshot dirs, sha256 manifest.

Downloaded bytes are untrusted. They are written to disk under
data/raw/<source>/<date>/ and never executed, imported or unpacked into a
directory that holds code.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import time
from pathlib import Path

import requests

from crisis_data.paths import RAW

USER_AGENT = "crisis-data/0.1 (open price panel research)"
SESSION = requests.Session()
SESSION.headers["User-Agent"] = USER_AGENT


def snapshot_dir(source: str, date: str | None = None) -> Path:
    date = date or dt.date.today().isoformat()
    d = RAW / source / date
    d.mkdir(parents=True, exist_ok=True)
    return d


def get(url: str, params: dict | None = None, retries: int = 4, timeout: int = 300) -> requests.Response:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {r.url}")
        except requests.RequestException as e:  # network hiccup
            last = e
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"fetch failed after {retries} tries: {url} {params}: {last}")


def write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(snap: Path, meta: dict) -> Path:
    """sha256sum-compatible MANIFEST.sha256 over every data file, plus fetch_meta.json."""
    meta_path = snap / "fetch_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    files = sorted(p for p in snap.rglob("*") if p.is_file() and p.name != "MANIFEST.sha256")
    lines = [f"{sha256(p)}  {p.relative_to(snap).as_posix()}" for p in files]
    man = snap / "MANIFEST.sha256"
    man.write_text("\n".join(lines) + "\n")
    return man


def verify_manifest(snap: Path) -> list[str]:
    """Return list of problems (empty when the snapshot matches its manifest)."""
    man = snap / "MANIFEST.sha256"
    if not man.exists():
        return [f"missing {man}"]
    bad = []
    listed = set()
    for line in man.read_text().splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        listed.add(name)
        p = snap / name
        if not p.exists():
            bad.append(f"missing {name}")
        elif sha256(p) != digest:
            bad.append(f"hash mismatch {name}")
    for p in snap.rglob("*"):
        if p.is_file() and p.name != "MANIFEST.sha256" and p.relative_to(snap).as_posix() not in listed:
            bad.append(f"unlisted {p.relative_to(snap).as_posix()}")
    return bad


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
