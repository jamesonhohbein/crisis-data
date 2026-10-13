"""Parse REACH JMMI workbooks (data/raw/jmmi/<date>/<iso3>/*.xlsx) into price observations.

Workbooks are untrusted: opened with openpyxl read_only + data_only (cached
values, no formula evaluation, no macros). Only district-level median sheets
are read; a district (admin2 pcode) is the JMMI "market".

Sudan: analysis-table workbooks, sheet "Median price items (districts)",
columns "<Item> price" (SDG, already converted by REACH to a per-kg or per-litre
price; checked against WFP Sudan per-kg prices for Aug 2026). Vendor-level
dataset workbooks are skipped when an analysis table covers the month.

Yemen: dataset workbooks. 2024 rounds: sheet "District Medians-Single",
columns calc_price_<item> (YER per kg or litre, cooking gas per cylinder).
2025+ rounds: sheet "medians_analysis" rows with admin_level_aggregation ==
admin2, columns <item>_price_item (YER per standard unit, per the Kobo form:
1 kg, 1 L, cooking gas 1 L). The district exchange-rate column becomes an FX
observation (market level, parallel rate as reported by vendors).
"""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import pandas as pd

from crisis_data.build.load import FX_COLS, OBS_COLS, _f, _rel
from crisis_data.paths import START_MONTH

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}
MONTH_RE = re.compile(r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[-_ .]*(20\d\d)", re.I)

SDN_SHEET = "Median price items (districts)"
YEM_SHEET_2024 = "District Medians-Single"
YEM_SHEET_2025 = "medians_analysis"

# Units of the REACH-standardised prices. Items not listed get unit_src "unknown" and are
# dropped as unit_unmapped if a commodity mapping ever points at them.
SDN_UNITS = {"Sorghum": "kg", "Onions": "kg", "Ful masri": "kg", "Veg oil": "L", "Sugar": "kg", "Tomato": "kg",
             "Cow meat": "kg", "Goat meat": "kg", "Wheat flour": "kg", "Millet": "kg", "Rice": "kg",
             "Lentils": "kg", "Salt": "kg", "Water supply": "20L"}
YEM_UNITS_2024 = {"wheat_flour": "kg", "rice": "kg", "beans_dry": "kg", "lentil": "kg", "vegetable_oil": "L",
                  "sugar": "kg", "salt": "kg", "potato": "kg", "onion": "kg", "petrol": "L", "diesel": "L",
                  "cooking_gas": "cylinder_26.5L"}
YEM_UNITS_2025 = {"wheat_flour": "kg", "rice": "kg", "red_bean": "kg", "lentil": "kg", "vegetable_oil": "L",
                  "sugar": "kg", "salt": "kg", "potato": "kg", "onion": "kg", "petrol": "L", "diesel": "L",
                  "cooking_fuel": "L"}


def month_from_name(name: str) -> str | None:
    """Last <month-name> <year> in the file name (round names end with the collection month)."""
    hits = MONTH_RE.findall(name)
    if not hits:
        return None
    mon, year = hits[-1]
    return f"{year}-{MONTHS[mon[:3].lower()]:02d}"


def _rows(ws) -> list[tuple]:
    return [r for r in ws.iter_rows(values_only=True)]


SDN_P1 = re.compile(r"^SD\d{2}$")
SDN_P2 = re.compile(r"^SD\d{5}$")


def _sdn_sheet(wb) -> str | None:
    # Older workbooks truncate the name to Excel's 31 characters: "Median price items (districts".
    return next((s for s in wb.sheetnames if s.startswith(SDN_SHEET[:28])), None)


def _sdn_table(wb) -> tuple[str, int, list[str], list[tuple]]:
    """Sheet name, 0-based header row index, header cells, all rows. The header row is the
    first row holding "Sorghum price"; layouts differ by round (title rows, unit rows, and
    id columns that are pcodes, names, or both, in varying order and with wrong labels)."""
    sheet = _sdn_sheet(wb)
    rows = _rows(wb[sheet])
    for i, r in enumerate(rows[:10]):
        hdr = [str(h).strip() if h is not None else "" for h in r]
        if "Sorghum price" in hdr:
            return sheet, i, hdr, rows
    raise ValueError(f"{sheet}: no header row with 'Sorghum price'")


def _sdn(path: Path, wb, month: str, ref: str) -> list[dict]:
    """Id columns are everything left of the first price column. Each cell is classed by
    value, not by its (often mislabelled) header: SDnn = state pcode, SDnnnnn = district
    pcode, other text = a name (last one = district, first of two = state)."""
    sheet, h, hdr, rows = _sdn_table(wb)
    pcols = {hh[: -len(" price")]: i for i, hh in enumerate(hdr) if hh.endswith(" price")}
    first = min(pcols.values())
    out = []
    for n, r in enumerate(rows[h + 1:], start=h + 2):
        p1 = p2 = None
        names = []
        for c in r[:first]:
            if c is None:
                continue
            t = str(c).strip()
            if SDN_P2.match(t):
                p2 = t
            elif SDN_P1.match(t):
                p1 = t
            elif t and not re.fullmatch(r"[\d.]+", t):
                names.append(t)
        if not (p2 or names):
            continue
        district = names[-1] if names else p2
        state = names[0] if len(names) >= 2 else None
        mid = p2 or f"{state or ''}|{district}"
        for it, col in sorted(pcols.items(), key=lambda kv: kv[1]):
            v = _f(r[col]) if col < len(r) else None
            if v is None or v <= 0:
                continue
            out.append(dict(
                source="jmmi", sub_source="REACH JMMI", country="SDN", market_id_src=mid,
                market_name_src=district, admin1_src=state, admin2_src=district, admin1_pcode_src=p1,
                admin2_pcode_src=p2, lat=None, lon=None, commodity_src=it,
                unit_src=SDN_UNITS.get(it, "unknown"), price_type="Retail", price_local=v, currency="SDG",
                obs_date=f"{month}-15", month=month,
                raw_ref=f"{ref}#{sheet}!R{n}C{col + 1}",
            ))
    return out


def _yem(path: Path, wb, month: str, ref: str, fx: list) -> list[dict]:
    if YEM_SHEET_2025 in wb.sheetnames:
        sheet, units, suffix = YEM_SHEET_2025, YEM_UNITS_2025, "_price_item"
    else:
        sheet, units, suffix = YEM_SHEET_2024, YEM_UNITS_2024, None
    rows = _rows(wb[sheet])
    hdr = [str(h).strip() if h is not None else "" for h in rows[0]]
    idx = {h: i for i, h in enumerate(hdr)}
    if suffix:
        items = {h[: -len(suffix)]: i for h, i in idx.items() if h.endswith(suffix) and not h.endswith("_unit_item")}
        p1c, p2c, fxc = idx["admin1_label"], idx["admin2_label"], idx.get("exchange_rate_buy_usd_yer")
        lvl = idx["admin_level_aggregation"]
    else:
        items = {h[len("calc_price_"):]: i for h, i in idx.items() if h.startswith("calc_price_")}
        p1c, p2c, fxc, lvl = idx["governorate_ID"], idx["district_ID"], idx.get("exchange_rate_result"), None
    out = []
    for n, r in enumerate(rows[1:], start=2):
        if lvl is not None and r[lvl] != "admin2":
            continue
        p2 = r[p2c]
        if not p2 or not str(p2).startswith("YE"):
            continue
        base = dict(source="jmmi", country="YEM", market_id_src=str(p2), month=month, lat=None, lon=None)
        name = r[idx["district_name"]] if "district_name" in idx else str(p2)
        for it, col in sorted(items.items()):
            v = _f(r[col])
            if v is None or v <= 0:
                continue
            out.append(dict(
                base, sub_source="REACH JMMI", market_name_src=name, admin1_src=str(r[p1c]),
                admin2_src=name, admin1_pcode_src=str(r[p1c]), admin2_pcode_src=str(p2), commodity_src=it,
                unit_src=units.get(it, "unknown"), price_type="Retail", price_local=v, currency="YER",
                obs_date=f"{month}-15", raw_ref=f"{ref}#{sheet}!R{n}C{col + 1}",
            ))
        if fxc is not None and _f(r[fxc]):
            fx.append(dict(base, level="market", admin1_src=str(r[p1c]), admin1_pcode_src=str(r[p1c]),
                           lcu_per_usd=_f(r[fxc]), rate_type="parallel (vendor reported)",
                           raw_ref=f"{ref}#{sheet}!R{n}C{fxc + 1}"))
    return out


def _classify(wb, iso3: str) -> str | None:
    s = set(wb.sheetnames)
    if iso3 == "SDN":
        return "sdn" if _sdn_sheet(wb) else None
    if YEM_SHEET_2025 in s or YEM_SHEET_2024 in s:
        return "yem"
    return None


def _files(snap: Path, iso3: str) -> list[Path]:
    return sorted((snap / iso3.lower()).glob("*.xlsx"))


def load_jmmi(snap: Path, drops: dict, fx_out: list | None = None) -> tuple[pd.DataFrame, dict]:
    """Returns (observations, notes). FX observations are appended to fx_out when given."""
    notes: dict = {"files_used": {}, "files_skipped": {}, "duplicate_months": {}}
    obs: list[dict] = []
    fx: list[dict] = []
    for iso3 in ("SDN", "YEM"):
        chosen: dict[str, tuple[int, Path, list, list]] = {}
        for path in _files(snap, iso3):
            rel = path.relative_to(snap).as_posix()
            month = month_from_name(path.name)
            if month is None:
                notes["files_skipped"][rel] = "no month in file name"
                continue
            if month < START_MONTH:
                notes["files_skipped"][rel] = f"before {START_MONTH}"
                continue
            wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
            try:
                kind = _classify(wb, iso3)
                if kind is None:
                    notes["files_skipped"][rel] = "no district median sheet (vendor-level or other layout)"
                    continue
                f_fx: list = []
                ref = _rel(snap, path)
                rows = _sdn(path, wb, month, ref) if kind == "sdn" else _yem(path, wb, month, ref, f_fx)
            finally:
                wb.close()
            if not rows:
                notes["files_skipped"][rel] = "district sheet has no prices"
                continue
            # Two workbooks for one round (re-issued tables): keep the one with more rows, tie by name.
            key = f"{iso3}|{month}"
            if key in chosen and len(rows) <= chosen[key][0]:
                notes["duplicate_months"].setdefault(key, []).append(rel)
                continue
            if key in chosen:
                notes["duplicate_months"].setdefault(key, []).append(chosen[key][1].relative_to(snap).as_posix())
            chosen[key] = (len(rows), path, rows, f_fx)
        for key, (_, path, rows, f_fx) in sorted(chosen.items()):
            notes["files_used"][path.relative_to(snap).as_posix()] = key.split("|")[1]
            obs.extend(rows)
            fx.extend(f_fx)
    for d in notes["duplicate_months"].values():
        d.sort()
    if fx_out is not None:
        fx_out.extend(fx)
    df = pd.DataFrame(obs, columns=OBS_COLS)
    return df, notes


def load_jmmi_fx(fx_rows: list) -> pd.DataFrame:
    return pd.DataFrame(fx_rows, columns=FX_COLS)


def raw_max_month(snap: Path, iso3: str) -> str:
    """Latest month of any JMMI workbook for iso3 with at least one positive numeric value in
    a district median price column. Independent of the crosswalks."""
    best = ""
    for path in _files(snap, iso3):
        month = month_from_name(path.name)
        if not month or month <= best:
            continue
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            kind = _classify(wb, iso3)
            if kind is None:
                continue
            if kind == "sdn":
                _, h, hdr, rows = _sdn_table(wb)
                rows = rows[h + 1:]
            else:
                sheet = YEM_SHEET_2025 if YEM_SHEET_2025 in wb.sheetnames else YEM_SHEET_2024
                rows = _rows(wb[sheet])
                hdr = [str(x or "") for x in rows[0]]
                rows = rows[1:]
        finally:
            wb.close()
        cols = [i for i, x in enumerate(hdr) if x.endswith(" price") or x.endswith("_price_item")
                or x.startswith("calc_price_")]
        if any(i < len(r) and (_f(r[i]) or 0) > 0 for r in rows for i in cols):
            best = month
    return best
