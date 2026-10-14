"""raw -> panel_long, panel_merged, coverage, coverage_summary, build_report.

Determinism (T8): every output is sorted on a full key, written without
timestamps, and Parquet is written by pyarrow with fixed options. The build
report carries snapshot names, not wall-clock time.
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from crisis_data.build import fx as fxmod
from crisis_data.build import markets as mk
from crisis_data.build.load import load_all
from crisis_data.paths import OUT, REF, START_MONTH

PANEL_COLS = [
    "source", "country", "admin1_pcode", "admin2_pcode", "market_id", "market_name_src", "lat", "lon",
    "commodity", "commodity_src", "unit_src", "qty_factor", "price_local", "currency", "currency_zone",
    "fx_rate", "fx_source", "price_usd", "price_per_std_unit", "month", "obs_date", "raw_ref",
]
EXTRA_COLS = ["std_unit", "sub_source", "match_method"]
SOURCE_PRIORITY = {"jmmi": 1, "wfp": 2, "fewsnet": 3}
DISAGREE_THRESHOLD = 0.20
LOST_WINDOW_MONTHS = 6


def _count(d: dict, k: str, n: int) -> None:
    if n:
        d[k] = d.get(k, 0) + int(n)


def _month_add(m: str, k: int) -> str:
    y, mo = int(m[:4]), int(m[5:7])
    t = y * 12 + (mo - 1) + k
    return f"{t // 12:04d}-{t % 12 + 1:02d}"


def _months(a: str, b: str) -> list[str]:
    out, m = [], a
    while m <= b:
        out.append(m)
        m = _month_add(m, 1)
    return out


def _write(df: pd.DataFrame, name: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    df = df.reset_index(drop=True)
    df.to_csv(out / f"{name}.csv", index=False, lineterminator="\n", float_format="%.10g")
    table = pa.Table.from_pandas(df, preserve_index=False)
    table = table.replace_schema_metadata(None)
    pq.write_table(table, out / f"{name}.parquet", compression="zstd", write_statistics=False,
                   use_dictionary=False, store_schema=False)


def build(out: Path | None = None, raw: Path | None = None, write_review: bool = True) -> dict:
    out = out or OUT
    loaded = load_all(raw)
    obs, fxo, drops = loaded["obs"], loaded["fx"], dict(loaded["drops"])
    report: dict = {"snapshots": loaded["snapshots"], "jmmi": loaded["jmmi_notes"], "start_month": START_MONTH}

    # ---- markets ---------------------------------------------------------
    mcols = ["source", "country", "market_id_src", "market_name_src", "admin1_src", "admin2_src",
             "admin1_pcode_src", "admin2_pcode_src", "lat", "lon"]
    fx_m = fxo.rename(columns={})[["source", "country", "market_id_src", "admin1_src", "lat", "lon"]].copy()
    fx_m["market_name_src"] = fx_m.market_id_src
    fx_m["admin2_src"] = None
    fx_m["admin1_pcode_src"] = None
    fx_m["admin2_pcode_src"] = None
    allm = pd.concat([obs[mcols], fx_m[mcols]], ignore_index=True)
    allm["market_id_src"] = allm.market_id_src.astype(str)
    uniq = allm.drop_duplicates(["source", "country", "market_id_src"]).sort_values(
        ["country", "source", "market_id_src"], kind="stable")
    matched, review = mk.match_markets(uniq, loaded["codab"])
    if write_review:
        mk.write_review(review)
    obs = obs.assign(market_id_src=obs.market_id_src.astype(str)).merge(
        matched, on=["source", "country", "market_id_src"], how="left")
    fxo = fxo.assign(market_id_src=fxo.market_id_src.astype(str)).merge(
        matched[["source", "country", "market_id_src", "admin1_pcode", "admin2_pcode"]],
        on=["source", "country", "market_id_src"], how="left")
    zones = fxmod.load_zones()
    obs["currency_zone"] = fxmod.assign_zones(obs, zones)
    fxo["currency_zone"] = fxmod.assign_zones(fxo, zones) if len(fxo) else None
    # national-level FEWS rates (no admin1) apply to every zone-less Sudan row
    fxo.loc[fxo.level == "national", "currency_zone"] = None

    # ---- coverage (all observations, before commodity/unit filters) -------
    obs_cov = obs.copy()

    # ---- panel filters, each drop counted ---------------------------------
    units = pd.read_csv(REF / "units.csv", dtype=str, keep_default_na=False)
    units["qty_factor"] = pd.to_numeric(units.qty_factor.replace("", None))
    comms = pd.read_csv(REF / "commodities.csv", keep_default_na=False)
    p = obs.copy()
    rt = p.price_type.fillna("").str.lower() != "retail"
    for k, n in p[rt].groupby("source").size().items():
        _count(drops, f"{k}:price_type_not_retail", n)
    p = p[~rt]
    p = p.merge(comms[["source", "commodity_src", "commodity", "priority"]], on=["source", "commodity_src"], how="left")
    for k, n in p[p.commodity.isna()].groupby("source").size().items():
        _count(drops, f"{k}:commodity_unmapped", n)
    report["unmapped_commodities"] = {
        f"{s}|{c}": int(n) for (s, c), n in
        p[p.commodity.isna()].groupby(["source", "commodity_src"]).size().sort_index().items()}
    p = p[p.commodity.notna()]
    u = units[units.std_unit != ""][["source", "unit_src", "std_unit", "qty_factor"]]
    p = p.merge(u, on=["source", "unit_src"], how="left")
    for k, n in p[p.std_unit.isna()].groupby("source").size().items():
        _count(drops, f"{k}:unit_unmapped", n)
    p = p[p.std_unit.notna()]
    yem_nozone = (p.country == "YEM") & p.currency_zone.isna()
    for k, n in p[yem_nozone].groupby("source").size().items():
        _count(drops, f"{k}:yemen_no_currency_zone", n)
    p = p[~yem_nozone]

    # ---- FX ------------------------------------------------------------------
    p = fxmod.attach_fx(p, fxo)
    for k, n in p[p.fx_rate.isna()].groupby("source").size().items():
        _count(report.setdefault("rows_without_fx", {}), k, n)
    p["price_usd"] = p.price_local / p.fx_rate
    p["price_per_std_unit"] = p.price_usd / p.qty_factor
    p["market_id"] = p.source + ":" + p.market_id_src
    p = p.rename(columns={})
    panel_long = p[PANEL_COLS + EXTRA_COLS + ["priority"]].sort_values(
        ["country", "source", "market_id", "commodity", "month", "raw_ref"], kind="stable").reset_index(drop=True)

    # ---- merged view (DuckDB) ----------------------------------------------
    con = duckdb.connect(config={"threads": 1})
    con.register("pl", panel_long)
    panel_merged = con.execute(f"""
        WITH best AS (
          SELECT *, coalesce(admin2_pcode, admin1_pcode) AS mkey,
                 CASE WHEN admin2_pcode IS NULL THEN 'admin1' ELSE 'admin2' END AS market_level,
                 min(priority) OVER (PARTITION BY source, country, coalesce(admin2_pcode, admin1_pcode),
                                     commodity, month) AS bp
          FROM pl WHERE price_per_std_unit IS NOT NULL AND admin1_pcode IS NOT NULL
        ), per_src AS (
          SELECT country, admin1_pcode, mkey, market_level, commodity, std_unit, month, source,
                 min(currency_zone) AS currency_zone,
                 median(price_per_std_unit) AS usd_per_unit, count(DISTINCT market_id) AS n_markets
          FROM best WHERE priority = bp
          GROUP BY ALL
        ), ranked AS (
          SELECT *, CASE source WHEN 'jmmi' THEN 1 WHEN 'wfp' THEN 2 ELSE 3 END AS sp FROM per_src
        )
        SELECT country, min(admin1_pcode) AS admin1_pcode, mkey AS market_key, market_level, commodity, std_unit, month,
               arg_min(source, sp) AS source,
               arg_min(usd_per_unit, sp) AS price_per_std_unit_usd,
               arg_min(n_markets, sp) AS n_markets,
               min(currency_zone) AS currency_zone,
               string_agg(source, ',' ORDER BY sp) AS sources_present,
               max(usd_per_unit) FILTER (WHERE source='jmmi') AS usd_jmmi,
               max(usd_per_unit) FILTER (WHERE source='wfp') AS usd_wfp,
               max(usd_per_unit) FILTER (WHERE source='fewsnet') AS usd_fewsnet,
               CASE WHEN count(*) > 1 THEN max(usd_per_unit) / min(usd_per_unit) - 1 END AS max_rel_diff,
               coalesce(max(usd_per_unit) / min(usd_per_unit) - 1 > {DISAGREE_THRESHOLD}, false) AS disagree
        FROM ranked GROUP BY country, mkey, market_level, commodity, std_unit, month
        ORDER BY country, market_key, market_level, commodity, std_unit, month
    """).df()

    # ---- coverage -----------------------------------------------------------
    cod = loaded["codab"]
    a1 = pd.concat([pd.DataFrame([dict(country=iso, admin1_pcode=f["properties"]["adm1_pcode"],
                                       admin1_name=f["properties"]["adm1_name"]) for f in lay["admin1"]["features"]])
                    for iso, lay in cod.items()], ignore_index=True)
    obs_cov["pair"] = obs_cov.market_id_src + "|" + obs_cov.commodity_src.fillna("")
    max_month = obs_cov.month.max()
    con.register("oc", obs_cov[["source", "country", "admin1_pcode", "month", "pair", "market_id_src"]])
    con.register("pl2", panel_long[["source", "country", "admin1_pcode", "month", "market_id", "commodity"]])
    obs_counts = con.execute("""
        SELECT source, country, coalesce(admin1_pcode, 'UNMATCHED') AS admin1_pcode, month,
               count(DISTINCT pair) AS n_pairs, count(DISTINCT market_id_src) AS n_markets
        FROM oc GROUP BY ALL""").df()
    staple = con.execute("""
        SELECT source, country, coalesce(admin1_pcode, 'UNMATCHED') AS admin1_pcode, month,
               count(DISTINCT market_id || '|' || commodity) AS n_staple_pairs
        FROM pl2 GROUP BY ALL""").df()
    sources = sorted(set(obs_cov.source) | {"jmmi"})
    grid = pd.MultiIndex.from_product(
        [sources, _months(START_MONTH, max_month)], names=["source", "month"]).to_frame(index=False)
    a1u = pd.concat([a1, pd.DataFrame([dict(country=c, admin1_pcode="UNMATCHED", admin1_name="(unmatched)")
                                       for c in sorted(cod)])], ignore_index=True)
    grid = grid.merge(a1u, how="cross")
    cov = grid.merge(obs_counts, on=["source", "country", "admin1_pcode", "month"], how="left").merge(
        staple, on=["source", "country", "admin1_pcode", "month"], how="left")
    for c in ("n_pairs", "n_markets", "n_staple_pairs"):
        cov[c] = cov[c].fillna(0).astype("int64")
    # keep UNMATCHED rows only where something was observed
    cov = cov[(cov.admin1_pcode != "UNMATCHED") | (cov.n_pairs > 0)]
    seen = cov[cov.n_pairs > 0]
    last = seen.groupby(["source", "country", "admin1_pcode"]).month.max().rename("last_month_seen")
    first = seen.groupby(["source", "country", "admin1_pcode"]).month.min().rename("first_month_seen")
    cov = cov.merge(last.reset_index(), on=["source", "country", "admin1_pcode"], how="left")
    cov = cov.sort_values(["source", "country", "admin1_pcode", "month"], kind="stable").reset_index(drop=True)

    sc_max = seen.groupby(["source", "country"]).month.max().rename("source_country_last_month").reset_index()
    seen24 = seen[seen.month.str.startswith("2024")].groupby(["source", "country", "admin1_pcode"]).size().rename(
        "months_seen_2024")
    summ = a1u[a1u.admin1_pcode != "UNMATCHED"].merge(pd.DataFrame({"source": sources}), how="cross")
    summ = summ.merge(first.reset_index(), how="left").merge(last.reset_index(), how="left").merge(
        seen24.reset_index(), how="left").merge(sc_max, how="left")
    summ["months_seen_2024"] = summ.months_seen_2024.fillna(0).astype("int64")
    cutoff = summ.source_country_last_month.map(
        lambda m: _month_add(m, -LOST_WINDOW_MONTHS) if isinstance(m, str) else None)
    summ["lost_since_2024"] = (summ.months_seen_2024 > 0) & (summ.last_month_seen <= cutoff)
    summ = summ.sort_values(["source", "country", "admin1_pcode"], kind="stable").reset_index(drop=True)

    # ---- report -------------------------------------------------------------
    report["drops"] = dict(sorted(drops.items()))
    report["rows"] = {
        "observations": int(len(obs_cov)),
        "panel_long": int(len(panel_long)),
        "panel_merged": int(len(panel_merged)),
        "panel_long_by_source_country": {f"{s}|{c}": int(n) for (s, c), n in
                                         panel_long.groupby(["source", "country"]).size().items()},
        "observations_by_source_country": {f"{s}|{c}": int(n) for (s, c), n in
                                           obs_cov.groupby(["source", "country"]).size().items()},
    }
    report["date_range"] = {f"{s}|{c}": [g.month.min(), g.month.max()]
                            for (s, c), g in obs_cov.groupby(["source", "country"])}
    report["last_month_seen"] = {f"{r.source}|{r.country}": r.source_country_last_month
                                 for r in sc_max.itertuples()}
    mm = panel_long.groupby(["source", "country"]).agg(
        n=("market_id", "size"), a1=("admin1_pcode", lambda s: int(s.notna().sum())),
        a2=("admin2_pcode", lambda s: int(s.notna().sum())))
    report["match_rate"] = {f"{s}|{c}": {"admin1": round(r.a1 / r.n, 4), "admin2": round(r.a2 / r.n, 4)}
                            for (s, c), r in mm.iterrows()}
    n = len(panel_long)
    report["match_rate"]["all"] = {"admin1": round(float(panel_long.admin1_pcode.notna().mean()), 4) if n else 0,
                                   "admin2": round(float(panel_long.admin2_pcode.notna().mean()), 4) if n else 0}
    report["fx_source"] = {k: int(v) for k, v in panel_long.fx_source.fillna("none").value_counts().sort_index().items()}
    report["merged_disagree_share"] = round(float(panel_merged.disagree.mean()), 4) if len(panel_merged) else 0
    report["merged_multi_source_cells"] = int((panel_merged.sources_present.str.contains(",")).sum())
    report["review_rows"] = int(len(review))
    report["lost_since_2024"] = {
        f"{s}|{c}": sorted(g[g.lost_since_2024].admin1_name.tolist())
        for (s, c), g in summ.groupby(["source", "country"])}
    used = panel_long[panel_long.fx_source.str.endswith(":market", na=False)][
        ["source", "market_id", "month"]].drop_duplicates()
    used_keys = set(zip(used.source, used.market_id.str.split(":", n=1).str[1], used.month))
    fxo_used = fxo[[k in used_keys for k in zip(fxo.source, fxo.market_id_src.astype(str), fxo.month)]]
    report["fx_zone_check"] = {"all_market_rates": zone_check(fxo), "rates_applied_to_panel": zone_check(fxo_used)}

    _write(panel_long.drop(columns=["priority"]), "panel_long", out)
    _write(panel_merged, "panel_merged", out)
    _write(cov, "coverage", out)
    _write(summ, "coverage_summary", out)
    # Every exchange rate the build could use, all sources. Local only: publish recomputes the
    # public panel's USD prices from the rows of publishable sources in this table.
    fx_cols = ["source", "country", "level", "market_id_src", "admin1_pcode", "admin2_pcode", "currency_zone",
               "month", "lcu_per_usd", "rate_type", "raw_ref"]
    fx_out = fxo.assign(market_id_src=fxo.market_id_src.astype(str))[fx_cols].sort_values(
        ["country", "source", "market_id_src", "month", "raw_ref"], kind="stable")
    _write(fx_out, "fx_rates", out)
    (out / "build_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return report


def zone_check(fxo: pd.DataFrame) -> dict:
    """Sanity check of ref/yemen_currency_zones.csv against the rates markets themselves report:
    within a month, a Sana'a-zone rate should sit well below every Aden-zone rate."""
    y = fxo[(fxo.country == "YEM") & fxo.currency_zone.notna()]
    if not len(y):
        return {}
    bad: list[str] = []
    total = 0
    for month, g in y.groupby("month"):
        aden = g[g.currency_zone == "aden"].lcu_per_usd
        sanaa = g[g.currency_zone == "sanaa"].lcu_per_usd
        if len(aden) and len(sanaa):
            thr = (aden.median() + sanaa.median()) / 2
            total += len(g)
            w = g[((g.currency_zone == "aden") & (g.lcu_per_usd < thr)) |
                  ((g.currency_zone == "sanaa") & (g.lcu_per_usd > thr))]
            bad += [f"{r.source}:{r.market_id_src}|{month}|{r.currency_zone}|{r.lcu_per_usd:g}|mid {thr:g}"
                    for r in w.itertuples()]
    return {"rates_checked": total, "rates_on_wrong_side_of_zone_midpoint": len(bad),
            "wrong_side": sorted(bad)}
