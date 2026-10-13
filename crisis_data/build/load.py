"""Parse raw snapshots into two uniform frames: price observations and FX observations.

Raw files are untrusted. They are read with csv/json/openpyxl/zipfile only;
nothing in data/raw is executed, imported or unpacked onto disk.

Price observation columns (OBS_COLS) carry every parsed row with a price,
before any commodity, unit or market filtering, so coverage can count what a
source reported even where the panel drops it.
"""

from __future__ import annotations

import csv
import json
import zipfile
from pathlib import Path

import pandas as pd

from crisis_data.fetch.fewsnet import iter_rows
from crisis_data.paths import RAW, START_MONTH, latest_snapshot

OBS_COLS = [
    "source", "sub_source", "country", "market_id_src", "market_name_src", "admin1_src", "admin2_src",
    "admin1_pcode_src", "admin2_pcode_src", "lat", "lon", "commodity_src", "unit_src", "price_type",
    "price_local", "currency", "obs_date", "month", "raw_ref",
]
FX_COLS = ["source", "country", "level", "market_id_src", "admin1_src", "admin1_pcode_src", "lat", "lon",
           "month", "lcu_per_usd", "rate_type", "raw_ref"]

WFP_FX_COMMODITIES = {"Exchange rate (unofficial)"}
WFP_PRICEFLAGS = {"actual", "actual,aggregate"}


def _rel(snap: Path, p: Path) -> str:
    return f"{snap.parent.name}/{snap.name}/{p.relative_to(snap).as_posix()}"


def _f(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_wfp(snap: Path, drops: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    obs, fx = [], []
    for iso3 in ("sdn", "yem"):
        path = snap / f"wfp_food_prices_{iso3}.csv"
        with open(path, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for row in rd:
                line = rd.line_num
                if row["date"][:7] < START_MONTH:
                    continue
                if row["priceflag"] not in WFP_PRICEFLAGS:
                    drops["wfp:priceflag_not_actual"] = drops.get("wfp:priceflag_not_actual", 0) + 1
                    continue
                price = _f(row["price"])
                if price is None:
                    drops["wfp:no_price"] = drops.get("wfp:no_price", 0) + 1
                    continue
                ref = f"{_rel(snap, path)}#L{line}"
                base = dict(
                    source="wfp", country=iso3.upper(), market_id_src=row["market_id"],
                    lat=_f(row["latitude"]), lon=_f(row["longitude"]), month=row["date"][:7], raw_ref=ref,
                )
                if row["commodity"] in WFP_FX_COMMODITIES:
                    fx.append(dict(base, level="market", admin1_src=row["admin1"], admin1_pcode_src=None,
                                   lcu_per_usd=price, rate_type="unofficial"))
                    continue
                obs.append(dict(
                    base, sub_source="WFP", market_name_src=row["market"], admin1_src=row["admin1"],
                    admin2_src=row["admin2"], admin1_pcode_src=None, admin2_pcode_src=None,
                    commodity_src=row["commodity"], unit_src=row["unit"], price_type=row["pricetype"],
                    price_local=price, currency=row["currency"], obs_date=row["date"],
                ))
    return pd.DataFrame(obs, columns=OBS_COLS), pd.DataFrame(fx, columns=FX_COLS)


def load_fewsnet(snap: Path, drops: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    obs, fx = [], []
    for iso3 in ("SDN", "YEM"):
        for page, line, r in iter_rows(snap, "marketpricefacts", iso3):
            month = (r.get("period_date") or "")[:7]
            if month < START_MONTH:
                continue
            if r.get("status") != "Collected" or _f(r.get("value")) is None:
                key = f"fewsnet:status_{(r.get('status') or 'none').lower().replace(' ', '_')}"
                drops[key] = drops.get(key, 0) + 1
                continue
            obs.append(dict(
                source="fewsnet", sub_source=r.get("source_organization"), country=iso3,
                market_id_src=r.get("fnid"), market_name_src=r.get("market"), admin1_src=r.get("admin_1"),
                admin2_src=r.get("admin_2"), admin1_pcode_src=None, admin2_pcode_src=None,
                lat=_f(r.get("latitude")), lon=_f(r.get("longitude")),
                commodity_src=r.get("product"), unit_src=r.get("unit"), price_type=r.get("price_type"),
                price_local=float(r["value"]), currency=r.get("currency"), obs_date=r.get("period_date"),
                month=month, raw_ref=f"{_rel(snap, page)}#L{line}",
            ))
        for page, line, r in iter_rows(snap, "exchangeratevaluefacts", iso3):
            month = (r.get("period_date") or "")[:7]
            if month < START_MONTH or _f(r.get("value")) is None or (r.get("status") or "Collected") != "Collected":
                continue
            if r.get("indicator") != "ExchangeRate:domestic_per_usd":
                continue
            fx.append(dict(
                source="fewsnet", country=iso3, level="market" if r.get("admin_1") else "national",
                market_id_src=r.get("fnid"), admin1_src=r.get("admin_1") or None, admin1_pcode_src=None,
                lat=_f(r.get("latitude")), lon=_f(r.get("longitude")), month=month,
                lcu_per_usd=float(r["value"]), rate_type=(r.get("rate_type") or "").lower(),
                raw_ref=f"{_rel(snap, page)}#L{line}",
            ))
    return pd.DataFrame(obs, columns=OBS_COLS), pd.DataFrame(fx, columns=FX_COLS)


def load_codab(snap: Path) -> dict[str, dict[str, dict]]:
    """{iso3: {"admin1": geojson, "admin2": geojson}} read from the zips in memory."""
    out: dict[str, dict[str, dict]] = {}
    for iso3 in ("sdn", "yem"):
        with zipfile.ZipFile(snap / f"{iso3}_admin_boundaries.geojson.zip") as z:
            out[iso3.upper()] = {
                lvl: json.loads(z.read(f"{iso3}_{lvl}.geojson").decode("utf-8")) for lvl in ("admin1", "admin2")
            }
    return out


def load_all(raw: Path | None = None) -> dict:
    """Load every source's latest snapshot. Missing JMMI is allowed (recorded, not fatal)."""
    from crisis_data.build import jmmi_load  # local import keeps openpyxl out of the hot path

    raw = raw or RAW
    drops: dict[str, int] = {}
    snaps = {s: latest_snapshot(s, raw) for s in ("wfp", "fewsnet", "codab")}
    wfp_obs, wfp_fx = load_wfp(snaps["wfp"], drops)
    fn_obs, fn_fx = load_fewsnet(snaps["fewsnet"], drops)
    jm_fx_rows: list = []
    try:
        snaps["jmmi"] = latest_snapshot("jmmi", raw)
        jm_obs, jm_notes = jmmi_load.load_jmmi(snaps["jmmi"], drops, jm_fx_rows)
    except FileNotFoundError:
        jm_obs, jm_notes = pd.DataFrame(columns=OBS_COLS), {"status": "no snapshot"}
    obs = pd.concat([d for d in (wfp_obs, fn_obs, jm_obs) if len(d)], ignore_index=True)
    jm_fx = jmmi_load.load_jmmi_fx(jm_fx_rows)
    fx = pd.concat([d for d in (wfp_fx, fn_fx, jm_fx) if len(d)], ignore_index=True)
    return {"obs": obs, "fx": fx, "codab": load_codab(snaps["codab"]), "drops": drops,
            "snapshots": {k: f"{v.parent.name}/{v.name}" for k, v in snaps.items()}, "jmmi_notes": jm_notes}
