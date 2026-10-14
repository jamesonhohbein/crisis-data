"""Cholera fetchers (spec 002). Raw goes to data/raw/cholera/<source>/<date>/ with a sha256 manifest.

Downloads are untrusted data: PDFs are read by pdfplumber in build, JSON with json, parquet with
pyarrow, zips with zipfile. Nothing here is executed.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from html import unescape

from crisis_data.fetch.common import get, now_iso, snapshot_dir, write_bytes, write_manifest

NCDC_BASE = "https://ncdc.gov.ng"
NCDC_LIST = NCDC_BASE + "/diseases/sitreps/"
NCDC_PARAMS = {"cat": "7", "name": "An update of Cholera outbreak in Nigeria"}
# Spec scope: 2024 W1 to 2025 W9 (the series ended with the 2025 W9 issue).
NCDC_FIRST, NCDC_LAST = (2024, 1), (2025, 9)

SDN_LAYER = ("https://services.arcgis.com/5T5nSi527N4F7luB/arcgis/rest/services/"
             "Sudan_Weekly_Cholera_Cases_WFL1/FeatureServer/0")
SDN_FIELDS = "OBJECTID,adm1_name,adm1_pcode,adm0_pcode,Year,Weeks,WeekNum,ISO_Weeks,Province,Cases"

WHO_LAYER = ("https://services.arcgis.com/5T5nSi527N4F7luB/arcgis/rest/services/"
             "cholera_adm0_week/FeatureServer/0")
WHO_HDX = "world-health-organization-who-cholera-data"
WHO_ISO3 = ("NGA", "SDN", "SSD")

OSF_NODE = "2ncf7"
JHU_FILES = {"Public_surveillance_dataset.parquet": "w8s7x", "data_dictionaries.xlsx": "8sf35"}

HDX_API = "https://data.humdata.org/api/3/action/package_show"
CODAB = {"NGA": "cod-ab-nga", "SDN": "cod-ab-sdn"}

# South Sudan verdict sample: MoH/WHO cholera sitreps hosted by WHO AFRO, spanning the outbreak.
SSD_SITREPS = {
    "ssd_sitrep_010.pdf": "https://www.afro.who.int/sites/default/files/2025-01/"
                          "South%20Sudan%20Cholera%20Situation%20Report_Issue%20%2310.pdf",
    "ssd_sitrep_013.pdf": "https://www.afro.who.int/sites/default/files/2025-01/"
                          "South%20Sudan%20Cholera%20Situation%20Report_Issue%20%2313.pdf",
    "ssd_sitrep_024.pdf": "https://www.afro.who.int/sites/default/files/2025-04/"
                          "South%20Sudan%20Cholera%20Situation%20Report%20%2324_%20_0.pdf",
}


def ncdc_listing(html: str) -> list[dict]:
    """Rows of the NCDC sitrep listing: title week, issue date (from the download name), href."""
    out = []
    for m in re.finditer(r'<td>(\d+)</td>\s*<td>([^<]*)</td>.*?href="([^"]+\.pdf)"\s+download="([^"]+)"',
                         html, re.S):
        title, href, dl = unescape(m.group(2)).strip(), m.group(3), unescape(m.group(4))
        wk = re.search(r"Week\s+(\d+)", title)
        d = re.search(r"_(\d{2})(\d{2})(\d{2})_(\d+)\.pdf$", dl)
        if not (wk and d):
            continue
        issued = dt.date(2000 + int(d.group(3)), int(d.group(2)), int(d.group(1)))
        week = int(wk.group(1))
        # an issue for week 52 dated in early January belongs to the previous year
        year = issued.year - 1 if (week >= 50 and issued.month == 1) else issued.year
        out.append({"title": title, "href": href, "download_name": dl, "issued": issued.isoformat(),
                    "year": year, "week": week})
    return out


def fetch_ncdc(date: str | None = None) -> dict:
    snap = snapshot_dir("cholera/ncdc", date)
    r = get(NCDC_LIST, params=NCDC_PARAMS)
    write_bytes(snap / "listing.html", r.content)
    rows = ncdc_listing(r.text)
    want = [x for x in rows if NCDC_FIRST <= (x["year"], x["week"]) <= NCDC_LAST]
    meta = {"source": "ncdc", "fetched_at": now_iso(), "listing_url": r.url, "listed_in_scope": len(want),
            "files": {}}
    for x in sorted(want, key=lambda x: (x["year"], x["week"])):
        name = f"ncdc_{x['year']}_w{x['week']:02d}.pdf"
        p = snap / name
        if not p.exists():
            write_bytes(p, get(NCDC_BASE + x["href"]).content)
            time.sleep(1)
        meta["files"][name] = {**x, "url": NCDC_BASE + x["href"]}
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "issues": len(meta["files"])}


def _arcgis_all(layer: str, where: str, fields: str, order: str) -> tuple[int, list[dict]]:
    count = get(layer + "/query", params={"where": where, "returnCountOnly": "true", "f": "json"}).json()["count"]
    feats, off = [], 0
    while True:
        d = get(layer + "/query", params={"where": where, "outFields": fields, "returnGeometry": "false",
                                          "orderByFields": order, "resultOffset": off,
                                          "resultRecordCount": 1000, "f": "json"}).json()
        if "error" in d:
            raise RuntimeError(f"{layer}: {d['error']}")
        feats += d["features"]
        off += len(d["features"])
        if not d.get("exceededTransferLimit") or not d["features"]:
            break
    return count, feats


def fetch_sdn(date: str | None = None) -> dict:
    snap = snapshot_dir("cholera/sdn_arcgis", date)
    svc = get(SDN_LAYER.rsplit("/", 1)[0], params={"f": "json"}).json()
    lay = get(SDN_LAYER, params={"f": "json"}).json()
    count, feats = _arcgis_all(SDN_LAYER, "1=1", SDN_FIELDS, "OBJECTID")
    write_bytes(snap / "service.json", json.dumps(svc, indent=1, sort_keys=True).encode())
    write_bytes(snap / "layer.json", json.dumps(lay, indent=1, sort_keys=True).encode())
    write_bytes(snap / "features.json", json.dumps([f["attributes"] for f in feats], indent=1,
                                                   sort_keys=True).encode())
    meta = {"source": "sdn_arcgis", "fetched_at": now_iso(), "layer": SDN_LAYER, "return_count_only": count,
            "features": len(feats), "data_last_edit": (lay.get("editingInfo") or {}).get("dataLastEditDate")}
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "count": count, "features": len(feats)}


def fetch_who(date: str | None = None) -> dict:
    snap = snapshot_dir("cholera/who_adm0", date)
    where = "iso_3_code in (" + ",".join(f"'{c}'" for c in WHO_ISO3) + ")"
    count, feats = _arcgis_all(WHO_LAYER, where, "*", "ObjectId")
    write_bytes(snap / "adm0_week.json", json.dumps([f["attributes"] for f in feats], indent=1,
                                                    sort_keys=True).encode())
    pkg = get(HDX_API, params={"id": WHO_HDX}).json()["result"]
    write_bytes(snap / "hdx_package.json", json.dumps(pkg, indent=1, sort_keys=True).encode())
    res = pkg["resources"][0]
    write_bytes(snap / "hdx_adm0_annual.csv", get(res["url"]).content)
    meta = {"source": "who_adm0", "fetched_at": now_iso(), "layer": WHO_LAYER, "where": where,
            "return_count_only": count, "features": len(feats), "hdx_license": pkg.get("license_title"),
            "hdx_resource": res["url"]}
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "count": count, "features": len(feats)}


def fetch_jhu(date: str | None = None) -> dict:
    snap = snapshot_dir("cholera/jhu", date)
    node = get(f"https://api.osf.io/v2/nodes/{OSF_NODE}/", params={"embed": "license"}).json()
    write_bytes(snap / "osf_node.json", json.dumps(node, indent=1, sort_keys=True).encode())
    for name, fid in JHU_FILES.items():
        write_bytes(snap / name, get(f"https://osf.io/download/{fid}/").content)
    lic = node["data"]["embeds"]["license"]["data"]["attributes"]["name"]
    meta = {"source": "jhu", "fetched_at": now_iso(), "osf_node": OSF_NODE, "license": lic,
            "files": {n: f"https://osf.io/download/{i}/" for n, i in JHU_FILES.items()}}
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "license": lic}


def fetch_codab(date: str | None = None) -> dict:
    snap = snapshot_dir("cholera/codab", date)
    meta = {"source": "codab", "fetched_at": now_iso(), "files": {}}
    for iso3, ds in CODAB.items():
        pkg = get(HDX_API, params={"id": ds}).json()["result"]
        res = [r for r in pkg["resources"] if r.get("format", "").lower() == "geojson"][0]
        name = f"{iso3.lower()}_admin_boundaries.geojson.zip"
        write_bytes(snap / name, get(res["url"]).content)
        meta["files"][name] = {"url": res["url"], "dataset": ds, "license": pkg.get("license_title")}
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "files": sorted(meta["files"])}


def fetch_ssd(date: str | None = None) -> dict:
    snap = snapshot_dir("cholera/ssd_sitreps", date)
    meta = {"source": "ssd_sitreps", "fetched_at": now_iso(), "files": {}}
    for name, url in SSD_SITREPS.items():
        write_bytes(snap / name, get(url).content)
        meta["files"][name] = {"url": url}
        time.sleep(1)
    write_manifest(snap, meta)
    return {"snapshot": str(snap), "files": sorted(meta["files"])}


FETCHERS = {"ncdc": fetch_ncdc, "sdn_arcgis": fetch_sdn, "who_adm0": fetch_who, "jhu": fetch_jhu,
            "codab": fetch_codab, "ssd_sitreps": fetch_ssd}
