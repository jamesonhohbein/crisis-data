"""Regenerate ref/yemen_currency_zones.csv from an ACAPS areas-of-control XLSX.

Not part of `build`: the committed ref file is the contract and is edited by
hand if control changes. Run once with the XLSX path (extracted from the HDX
zip into its own empty directory) and the COD-AB Yemen zip:

    uv run python -I -m crisis_data.build.zones_ref <xlsx> <yem_admin_boundaries.geojson.zip>

Zone rule: ACAPS `IRG` districts use the Aden-issued rial (zone `aden`),
`DFA` districts the Sana'a rial (zone `sanaa`). Governorate rows (blank
adm2_pcode) carry the majority zone and only apply to markets matched to
admin1 but not admin2.
"""

from __future__ import annotations

import csv
import json
import sys
import zipfile
from collections import Counter

import openpyxl

from crisis_data.paths import REF

SOURCE = ("ACAPS Yemen Analysis Hub, Areas of control 2026-04-08 (CC BY), "
          "https://data.humdata.org/dataset/yemen-areas-of-control")
ZONE = {"IRG": "aden", "DFA": "sanaa"}


def generate(xlsx: str, cod_zip: str) -> None:
    ws = openpyxl.load_workbook(xlsx, read_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    assert rows[0][:2] == ("adm2_pcode", "areas_of_control"), rows[0]
    control = {r[0]: r[1] for r in rows[1:] if r[0]}
    with zipfile.ZipFile(cod_zip) as z:
        a2 = json.loads(z.read("yem_admin2.geojson"))["features"]
        a1 = json.loads(z.read("yem_admin1.geojson"))["features"]
    names2 = {f["properties"]["adm2_pcode"]: (f["properties"]["adm1_pcode"], f["properties"]["adm2_name"]) for f in a2}
    names1 = {f["properties"]["adm1_pcode"]: f["properties"]["adm1_name"] for f in a1}
    missing = set(names2) - set(control)
    assert not missing, f"districts without control: {sorted(missing)}"
    out = []
    by1: dict[str, Counter] = {}
    for p2, (p1, n2) in sorted(names2.items()):
        c = control[p2]
        by1.setdefault(p1, Counter())[c] += 1
        out.append([p1, names1[p1], p2, n2, c, ZONE[c], SOURCE])
    for p1, cnt in sorted(by1.items()):
        (c, n), *rest = cnt.most_common()
        note = f"governorate default = majority of districts ({dict(cnt)}); {SOURCE}"
        if rest and rest[0][1] == n:  # tie (Ma'rib 7/7): the IRG side holds the governorate capital
            c = "IRG"
            note = f"governorate default: tie {dict(cnt)}, IRG holds the governorate capital; {SOURCE}"
        out.append([p1, names1[p1], "", "", c, ZONE[c], note])
    out.sort(key=lambda r: (r[0], r[2]))
    with open(REF / "yemen_currency_zones.csv", "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["adm1_pcode", "adm1_name", "adm2_pcode", "adm2_name", "control", "zone", "source"])
        w.writerows(out)


if __name__ == "__main__":
    generate(sys.argv[1], sys.argv[2])
