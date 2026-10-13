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

import json
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
        # The walk is slow (10 s per page), so progress is kept in the snapshot and a rerun of
        # the same snapshot date resumes where it stopped.
        prog_path = snap / f"listing_{iso3.lower()}.json"
        prog = json.loads(prog_path.read_text()) if prog_path.exists() else \
            {"next_page": 1, "stale": 0, "done": False, "found": {}, "pages": {}, "seen": []}
        while not prog["done"] and prog["next_page"] <= MAX_PAGES:
            page = prog["next_page"]
            r = get(LISTING.format(country=slug), params={"paging": page})
            time.sleep(CRAWL_DELAY_S)
            html = r.text
            all_docs = re.findall(r"repository\.impact-initiatives\.org/document/impact/[0-9a-f]+/([^\"'<> ]+)", html)
            prog["pages"][str(page)] = len(set(all_docs))
            new = set(all_docs) - set(prog.setdefault("seen", []))
            if not new:  # empty page, or past the end (the site repeats sidebar links only)
                prog["done"] = True
            prog["seen"] = sorted(set(prog["seen"]) | set(all_docs))
            for docid, name in LINK.findall(html):
                if _is_jmmi(name):
                    prog["found"].setdefault(f"{docid}__{name}", [docid, name, page])
            ys = _years(all_docs)
            prog["stale"] = prog["stale"] + 1 if ys and max(ys) < 2023 else 0
            if prog["stale"] >= STALE_PAGES_STOP:
                prog["done"] = True
            prog["next_page"] = page + 1
            write_bytes(prog_path, json.dumps(prog, indent=1, sort_keys=True).encode())
        found = prog["found"]
        meta["pages"].update({f"{iso3}:{k}": v for k, v in prog["pages"].items()})
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
