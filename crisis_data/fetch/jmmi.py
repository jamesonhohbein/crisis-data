"""REACH / IMPACT JMMI workbooks (XLSX only; PDFs are never fetched).

repository.impact-initiatives.org has no public search or API (its HTML
redirects to a login). The public country pages on impact-initiatives.org
list every document with a direct repository link, so we walk
`/where-we-work/<country>/?paging=N` and keep links to `.xlsx` files whose
name mentions JMMI. Both hosts publish `Crawl-delay: 10`, which we honour.

Files land in data/raw/jmmi/<date>/<iso3>/<docid>__<filename>.xlsx with an
index.json of where each came from.
"""

from __future__ import annotations

import re
import time

from crisis_data.fetch.common import get, now_iso, snapshot_dir, write_bytes, write_manifest

LISTING = "https://www.impact-initiatives.org/where-we-work/{country}/"
COUNTRIES = {"SDN": "sudan", "YEM": "yemen"}
CRAWL_DELAY_S = 10.0
MAX_PAGES = 90
LINK = re.compile(r"https://repository\.impact-initiatives\.org/document/impact/([0-9a-f]+)/([^\"'<> ]+?\.xlsx)",
                  re.I)
YEAR = re.compile(r"20(1\d|2[0-9])")
# Old rounds are irrelevant (panel starts 2023-01); stop after this many pages in a row
# whose file names only carry years before 2023.
STALE_PAGES_STOP = 4


def _is_jmmi(name: str) -> bool:
    n = name.lower()
    return "jmmi" in n or "joint-market-monitoring" in n


def _years(names: list[str]) -> set[int]:
    return {2000 + int(m) for n in names for m in YEAR.findall(n)}


def fetch(date: str | None = None) -> dict:
    snap = snapshot_dir("jmmi", date)
    meta = {"source": "jmmi", "fetched_at": now_iso(), "files": {}, "pages": {}}
    for iso3, slug in COUNTRIES.items():
        found: dict[str, tuple[str, str, int]] = {}
        stale = 0
        for page in range(1, MAX_PAGES + 1):
            r = get(LISTING.format(country=slug), params={"paging": page})
            time.sleep(CRAWL_DELAY_S)
            html = r.text
            all_docs = re.findall(r"repository\.impact-initiatives\.org/document/impact/[0-9a-f]+/([^\"'<> ]+)", html)
            meta["pages"][f"{iso3}:{page}"] = len(set(all_docs))
            if not all_docs:
                break
            for docid, name in LINK.findall(html):
                if _is_jmmi(name):
                    found.setdefault(f"{docid}__{name}", (docid, name, page))
            ys = _years(all_docs)
            stale = stale + 1 if ys and max(ys) < 2023 else 0
            if stale >= STALE_PAGES_STOP:
                break
        for key, (docid, name, page) in sorted(found.items()):
            url = f"https://repository.impact-initiatives.org/document/impact/{docid}/{name}"
            rel = f"{iso3.lower()}/{key}"
            if not (snap / rel).exists():  # resume: files are written atomically
                resp = get(url)
                time.sleep(CRAWL_DELAY_S)
                write_bytes(snap / rel, resp.content)
            meta["files"][rel] = {"url": url, "listing_page": page}
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "files": len(meta["files"])}
