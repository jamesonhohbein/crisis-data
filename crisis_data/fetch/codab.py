"""OCHA COD-AB administrative boundaries (HDX, CC BY-IGO), GeoJSON zip per country.

The zip is stored as downloaded. Build reads the admin1/admin2 layers straight
out of the zip with zipfile + json, so nothing is unpacked to disk.
"""

from __future__ import annotations

from crisis_data.fetch.common import get, now_iso, snapshot_dir, write_bytes, write_manifest

HDX_API = "https://data.humdata.org/api/3/action/package_show"
DATASETS = {"SDN": "cod-ab-sdn", "YEM": "cod-ab-yem"}


def fetch(date: str | None = None) -> dict:
    snap = snapshot_dir("codab", date)
    meta = {"source": "codab", "fetched_at": now_iso(), "files": {}}
    for iso3, ds in DATASETS.items():
        pkg = get(HDX_API, params={"id": ds}).json()["result"]
        res = [r for r in pkg["resources"] if r.get("format", "").lower() == "geojson"]
        if not res:
            raise RuntimeError(f"{ds}: no GeoJSON resource")
        url = res[0]["url"]
        name = f"{iso3.lower()}_admin_boundaries.geojson.zip"
        write_bytes(snap / name, get(url).content)
        meta["files"][name] = {"url": url, "dataset": ds, "last_modified": res[0].get("last_modified"),
                               "license": pkg.get("license_title")}
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "files": sorted(meta["files"])}
