"""WFP VAM food prices from HDX (CC BY-IGO). Whole-history CSV per country."""

from __future__ import annotations

from crisis_data.fetch.common import get, now_iso, snapshot_dir, write_bytes, write_manifest

HDX_API = "https://data.humdata.org/api/3/action/package_show"
DATASETS = {"SDN": "wfp-food-prices-for-sudan", "YEM": "wfp-food-prices-for-yemen"}


def fetch(date: str | None = None) -> dict:
    snap = snapshot_dir("wfp", date)
    meta = {"source": "wfp", "fetched_at": now_iso(), "files": {}}
    for iso3, ds in DATASETS.items():
        r = get(HDX_API, params={"id": ds})
        pkg = r.json()["result"]
        write_bytes(snap / f"hdx_package_{iso3.lower()}.json", r.content)
        for res in pkg["resources"]:
            url = res["url"]
            name = url.rsplit("/", 1)[-1]
            if not name.endswith(".csv"):
                continue
            write_bytes(snap / name, get(url).content)
            meta["files"][name] = {"url": url, "dataset": ds, "last_modified": res.get("last_modified"),
                                   "license": pkg.get("license_title")}
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "files": sorted(meta["files"])}
