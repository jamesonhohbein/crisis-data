"""Exchange rates and Yemen currency zones.

Yemen has two rials: the Aden-governed (IRG) rial and the Sana'a-governed
(DFA) rial trade roughly 3x apart. Every Yemen market gets a zone from
ref/yemen_currency_zones.csv (district row beats governorate row) and an FX
rate is only ever taken from the same zone.

Lookup chain per row, first hit wins (fx_source names the hit):
  1. same source, same market, same month                ("<src>:market")
  2. same source, same admin1 (and zone), same month      ("<src>:admin1")
  3. WFP, same admin1 (and zone), same month              ("wfp:admin1")
  4. FEWS NET, same admin1 (and zone), same month         ("fewsnet:admin1")
  5. WFP, country (Yemen: zone), same month, median       ("wfp:zone" / "wfp:national")
  6. FEWS NET, country (Yemen: zone), same month, median  ("fewsnet:zone" / "fewsnet:national")
Steps 1-3 are the spec rule; 4-6 are fallbacks added because WFP Sudan
reports a market rate for only Khartoum and Port Sudan. JMMI Yemen workbooks
carry a district rate, so JMMI Yemen rows normally stop at step 1.
"""

from __future__ import annotations

import pandas as pd

from crisis_data.paths import REF

ZONES_PATH = REF / "yemen_currency_zones.csv"


def load_zones() -> pd.DataFrame:
    return pd.read_csv(ZONES_PATH, dtype=str, keep_default_na=False, comment=None)


def zone_for(admin1_pcode, admin2_pcode, zones: pd.DataFrame) -> str | None:
    if not admin1_pcode:
        return None
    if admin2_pcode:
        z = zones[zones.adm2_pcode == admin2_pcode]
        if len(z):
            return z.iloc[0].zone
    z = zones[(zones.adm1_pcode == admin1_pcode) & (zones.adm2_pcode == "")]
    return z.iloc[0].zone if len(z) else None


MARKET_ZONES_PATH = ZONES_PATH.with_name("yemen_market_zones.csv")


def load_market_zones() -> dict[tuple[str, str], str]:
    """Per-market overrides for markets the district/governorate control map cannot place:
    area averages in split governorates and markets in split frontline districts. Each row
    cites its evidence."""
    if not MARKET_ZONES_PATH.exists():
        return {}
    m = pd.read_csv(MARKET_ZONES_PATH, dtype=str, keep_default_na=False)
    return {(r.source, r.market_id_src): r.zone for r in m.itertuples()}


def assign_zones(df: pd.DataFrame, zones: pd.DataFrame) -> pd.Series:
    over = load_market_zones()
    d2 = dict(zip(zones[zones.adm2_pcode != ""].adm2_pcode, zones[zones.adm2_pcode != ""].zone))
    d1 = dict(zip(zones[zones.adm2_pcode == ""].adm1_pcode, zones[zones.adm2_pcode == ""].zone))

    def f(r):
        if r.country != "YEM":
            return None
        if (r.source, str(r.market_id_src)) in over:
            return over[(r.source, str(r.market_id_src))]
        if r.admin2_pcode and r.admin2_pcode in d2:
            return d2[r.admin2_pcode]
        return d1.get(r.admin1_pcode) if r.admin1_pcode else None

    return df.apply(f, axis=1) if len(df) else pd.Series([], dtype=object)


def attach_fx(rows: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    """rows need: source,country,market_id_src,admin1_pcode,currency_zone,month,currency.
    fx needs: source,country,market_id_src,admin1_pcode,currency_zone,month,lcu_per_usd."""
    fx = fx.copy()
    fx["zone_key"] = fx.currency_zone.fillna("-")
    rows = rows.copy()
    rows["zone_key"] = rows.currency_zone.fillna("-")
    rows["fx_rate"] = pd.NA
    rows["fx_source"] = pd.NA

    def med(keys):
        return fx.groupby(keys, dropna=False).lcu_per_usd.median().rename("r").reset_index()

    steps = []
    by_mkt = med(["source", "country", "market_id_src", "month"])
    steps.append(("market", by_mkt, ["source", "country", "market_id_src", "month"], None))
    by_a1 = med(["source", "country", "admin1_pcode", "zone_key", "month"]).dropna(subset=["admin1_pcode"])
    steps.append(("admin1", by_a1, ["source", "country", "admin1_pcode", "zone_key", "month"], None))
    steps.append(("admin1", by_a1, ["country", "admin1_pcode", "zone_key", "month"], "wfp"))
    steps.append(("admin1", by_a1, ["country", "admin1_pcode", "zone_key", "month"], "fewsnet"))
    by_zone = med(["source", "country", "zone_key", "month"])
    steps.append(("zone", by_zone, ["country", "zone_key", "month"], "wfp"))
    steps.append(("zone", by_zone, ["country", "zone_key", "month"], "fewsnet"))

    for level, table, keys, fixed_src in steps:
        need = rows.fx_rate.isna()
        if not need.any():
            break
        t = table if fixed_src is None else table[table.source == fixed_src].drop(columns="source")
        sub = rows.loc[need, keys].merge(t, on=keys, how="left")
        sub.index = rows.index[need]
        hit = sub.r.notna()
        idx = sub.index[hit]
        rows.loc[idx, "fx_rate"] = sub.loc[hit, "r"].values
        if fixed_src is None:
            src = rows.loc[idx, "source"]
        else:
            src = pd.Series(fixed_src, index=idx)
        lvl = level
        if level == "zone":
            lvl_series = rows.loc[idx, "country"].map(lambda c: "zone" if c == "YEM" else "national")
            rows.loc[idx, "fx_source"] = src + ":" + lvl_series
        else:
            rows.loc[idx, "fx_source"] = src + ":" + lvl
    usd = rows.currency == "USD"
    rows.loc[usd, "fx_rate"] = 1.0
    rows.loc[usd, "fx_source"] = "none:usd"
    rows["fx_rate"] = pd.to_numeric(rows.fx_rate)
    return rows.drop(columns="zone_key")
