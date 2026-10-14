"""Cholera build (spec 002): admin1 rows in the JHU schema, checks, build report.

Inputs are the newest manifest-carrying snapshots under data/raw/cholera/. Outputs go to
data/out/cholera/ and are deterministic (sorted rows, no timestamps), so the same raw inputs give
byte-identical files (C8).

Row conventions (read alongside the JHU OSF release, whose columns and dtypes are copied at build
time):
- NCDC weekly rows: TL = Monday of the ISO epi week of the issue, TR = TL + 6 days. Blank weekly
  cells in a listed state's row are read as 0 (with this reading the state rows sum to the printed
  national row in every issue that prints one).
- NCDC cumulative rows: TL = Monday of ISO week 1, TR = Sunday of the issue week (year to date as
  printed). `obs_type` says which kind a row is; date_range is TR - TL + 1 day, as in JHU.
- Sudan rows: TL = the layer's own ISO_Weeks date, epiweek = the layer's Year-WeekNum label. The
  layer maps both 2024 wk53 and 2025 wk01 to 2024-12-30; both rows are kept as reported and flagged.
"""

from __future__ import annotations

import datetime as dt
import json
import re
import zipfile
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from crisis_data.cholera.ncdc_parse import parse
from crisis_data.paths import OUT, RAW, latest_snapshot

OUT_DIR = OUT / "cholera"
REGION = {"NGA": "AFR", "SDN": "EMR"}
NAME_ALIAS = {"fct": "federal capital territory", "fct abuja": "federal capital territory",
              "akwa ibom": "akwa ibom", "nassarawa": "nasarawa"}
EXTRA = ["country", "admin1_pcode", "admin1_name", "obs_type", "source", "source_doc", "vintage", "method",
         "current", "source_week"]


def _norm(s: str) -> str:
    s = re.sub(r"[^a-z ]", " ", s.lower().replace("-", " "))
    s = re.sub(r"\s+", " ", s).strip()
    return NAME_ALIAS.get(s, s)


def iso_monday(year: int, week: int) -> dt.date:
    return dt.date.fromisocalendar(year, week, 1)


def load_codab_admin1(snap: Path) -> dict[str, dict]:
    """{iso3: geojson} for admin1, read from the zips in memory."""
    out = {}
    for iso3 in ("nga", "sdn"):
        with zipfile.ZipFile(snap / f"{iso3}_admin_boundaries.geojson.zip") as z:
            out[iso3.upper()] = json.loads(z.read(f"{iso3}_admin1.geojson").decode("utf-8"))
    return out


def pcode_index(codab: dict) -> dict[str, dict[str, tuple[str, str]]]:
    return {iso: {_norm(f["properties"]["adm1_name"]): (f["properties"]["adm1_pcode"], f["properties"]["adm1_name"])
                  for f in g["features"]} for iso, g in codab.items()}


def jhu_template(snap: Path) -> pd.DataFrame:
    return pd.read_parquet(snap / "Public_surveillance_dataset.parquet")


# ---------------------------------------------------------------- Nigeria
def ncdc_rows(snap: Path, pidx: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """(admin1 rows, national rows as printed, issue log)."""
    meta = json.loads((snap / "fetch_meta.json").read_text())["files"]
    rows, nat, log = [], [], []
    for name in sorted(meta):
        m = meta[name]
        iss = parse(snap / name)
        year, week = iss.year, iss.week
        if (year, week) != (m["year"], m["week"]):
            iss.problems.append(f"table says {year} W{week}, listing says {m['year']} W{m['week']}")
            year, week = m["year"], m["week"]
        tl = iso_monday(year, week)
        doc = f"{name}#page={iss.page}"
        n = iss.national
        printed = n is not None and n.cum_cases is not None
        sums = {k: sum(getattr(r, k) or 0 for r in iss.states)
                for k in ("cum_cases", "cum_deaths", "week_cases", "week_deaths")}
        balanced = printed and (sums["cum_cases"], sums["cum_deaths"], sums["week_cases"], sums["week_deaths"]) == (
            n.cum_cases, n.cum_deaths, n.week_cases or 0, n.week_deaths or 0)
        log.append({"doc": name, "page": iss.page, "year": year, "week": week, "issued": m["issued"],
                    "url": m["url"], "states": len(iss.states), "national_printed": printed,
                    "balanced": bool(balanced), "problems": iss.problems, "state_sums": sums,
                    "national": None if not printed else {"cum_cases": n.cum_cases, "cum_deaths": n.cum_deaths,
                                                          "week_cases": n.week_cases or 0,
                                                          "week_deaths": n.week_deaths or 0}})
        if printed:
            nat.append({"year": year, "week": week, "TL": tl, **log[-1]["national"], "doc": doc})
        for r in iss.states:
            key = _norm(r.name)
            if key not in pidx["NGA"]:
                raise ValueError(f"{name}: state {r.name!r} has no COD-AB pcode")
            pcode, cname = pidx["NGA"][key]
            base = {"country": "NGA", "admin1_pcode": pcode, "admin1_name": cname, "original_location_name": r.name,
                    "location_name": f"AFR::NGA::{cname}", "source": "ncdc", "source_doc": doc,
                    "vintage": m["issued"], "method": "parse", "observation_collection_id": f"ncdc:{name}",
                    "source_week": f"{year}-W{week:02d}"}
            rows.append({**base, "obs_type": "weekly", "TL": tl, "TR": tl + dt.timedelta(days=6),
                         "epiweek": f"{year}-{week:02d}", "sCh": float(r.week_cases or 0),
                         "deaths": float(r.week_deaths or 0)})
            rows.append({**base, "obs_type": "cumulative", "TL": iso_monday(year, 1),
                         "TR": tl + dt.timedelta(days=6), "epiweek": f"{year}-{week:02d}",
                         "sCh": float(r.cum_cases) if r.cum_cases is not None else float("nan"),
                         "deaths": float(r.cum_deaths) if r.cum_deaths is not None else float("nan")})
    return rows, nat, log


# ---------------------------------------------------------------- Sudan
def sdn_rows(snap: Path, pidx: dict) -> tuple[list[dict], dict]:
    meta = json.loads((snap / "fetch_meta.json").read_text())
    feats = json.loads((snap / "features.json").read_text())
    vintage = dt.datetime.fromtimestamp(meta["data_last_edit"] / 1000, dt.UTC).date().isoformat()
    by_pcode = {p: n for d in pidx["SDN"].values() for p, n in [d]}
    rows, skipped = [], []
    for f in feats:
        if f["Cases"] is None or f["ISO_Weeks"] is None:
            skipped.append(f["OBJECTID"])
            continue
        pcode = f["adm1_pcode"]
        if pcode not in by_pcode:
            raise ValueError(f"Sudan feature {f['OBJECTID']}: pcode {pcode!r} not in COD-AB")
        tl = dt.date.fromisoformat(f["ISO_Weeks"])
        y, w = int(f["Year"]), int(f["WeekNum"])
        rows.append({"country": "SDN", "admin1_pcode": pcode, "admin1_name": by_pcode[pcode],
                     "original_location_name": f["Province"] or f["adm1_name"],
                     "location_name": f"EMR::SDN::{by_pcode[pcode]}", "source": "sdn_arcgis",
                     "source_doc": f"features.json#OBJECTID={f['OBJECTID']}", "vintage": vintage, "method": "api",
                     "observation_collection_id": f"sdn_arcgis:{snap.name}", "source_week": f"{y}-W{w:02d}",
                     "obs_type": "weekly", "TL": tl, "TR": tl + dt.timedelta(days=6), "epiweek": f"{y}-{w:02d}",
                     "sCh": float(f["Cases"]), "deaths": float("nan")})
    iso_mismatch = sorted({r["source_week"] for r in rows
                           if r["source_week"] != f"{r['TL'].isocalendar()[0]}-W{r['TL'].isocalendar()[1]:02d}"})
    return rows, {"features": len(feats), "return_count_only": meta["return_count_only"],
                  "skipped_null_rows": skipped, "vintage": vintage, "labels_not_matching_iso": iso_mismatch}


# ---------------------------------------------------------------- WHO adm0
def who_weekly(snap: Path) -> pd.DataFrame:
    """WHO adm0 weekly, keyed to the Monday of the ISO week containing WHO's own week date."""
    w = pd.DataFrame(json.loads((snap / "adm0_week.json").read_text()))
    d = pd.to_datetime(w.date_wk, unit="ms", utc=True).dt.date
    tl = [x - dt.timedelta(days=x.weekday()) for x in d]
    out = pd.DataFrame({"source": "who_adm0", "country": w.iso_3_code, "who_epiyr": w.epiyr.astype(int),
                        "who_epiwk": w.epiwk.astype(int), "who_date": [x.isoformat() for x in d],
                        "TL": [x.isoformat() for x in tl], "cases": w.cases.astype(float),
                        "deaths": w.deaths.astype(float)})
    return out.sort_values(["country", "TL", "who_epiyr", "who_epiwk"], kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- frame + checks
def to_jhu(rows: list[dict], tmpl: pd.DataFrame) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["cCh"] = float("nan")
    df["start_weekday"] = "Monday"
    df["spatial_scale"] = "admin1"
    df["composite_loc"] = False
    df["phantom"] = False
    df["location_period_id"] = None
    df["pop"] = float("nan")
    df["current"] = True
    df["date_range"] = [pd.Timedelta(days=(tr - tl).days + 1) for tl, tr in zip(df.TL, df.TR)]
    cols = list(tmpl.columns)
    df = df[cols + EXTRA]
    types = {c: tmpl[c].dtype for c in cols}
    df = df.astype(types)
    df = df.sort_values(["country", "admin1_pcode", "obs_type", "TL", "TR", "source_week", "source_doc"],
                        kind="stable").reset_index(drop=True)
    return df


def checks(df: pd.DataFrame, nat: list[dict], who: pd.DataFrame, sdn_meta: dict) -> pd.DataFrame:
    """One row per country x reported week: state sum vs printed national, continuity, WHO ratio."""
    out = []
    whok = {(r.country, r.TL): r.cases for r in who.itertuples()}
    natk = {(n["year"], n["week"]): n for n in nat}
    # Nigeria
    ng = df[df.country == "NGA"]
    wk = ng[ng.obs_type == "weekly"]
    cu = ng[ng.obs_type == "cumulative"]
    weeks = sorted({tuple(map(int, s[:4].split() + [s[6:]])) for s in wk.source_week})
    prev_cum: dict | None = None
    prev_key = None
    for y, w in weeks:
        sw = f"{y}-W{w:02d}"
        a, c = wk[wk.source_week == sw], cu[cu.source_week == sw]
        cum = dict(zip(c.admin1_pcode, c.sCh))
        weekly = dict(zip(a.admin1_pcode, a.sCh))
        n = natk.get((y, w))
        tl = iso_monday(y, w).isoformat()
        row = {"country": "NGA", "source_week": sw, "TL": tl, "states": len(a),
               "state_sum_week_cases": a.sCh.sum(), "state_sum_week_deaths": a.deaths.sum(),
               "state_sum_cum_cases": c.sCh.sum(), "state_sum_cum_deaths": c.deaths.sum(),
               "national_week_cases": n["week_cases"] if n else None,
               "national_week_deaths": n["week_deaths"] if n else None,
               "national_cum_cases": n["cum_cases"] if n else None,
               "national_cum_deaths": n["cum_deaths"] if n else None}
        row["balance"] = ("no printed national row" if n is None else
                          "ok" if (row["state_sum_week_cases"], row["state_sum_week_deaths"],
                                   row["state_sum_cum_cases"], row["state_sum_cum_deaths"]) ==
                          (n["week_cases"], n["week_deaths"], n["cum_cases"], n["cum_deaths"]) else "mismatch")
        consecutive = prev_key is not None and iso_monday(*prev_key) + dt.timedelta(days=7) == iso_monday(y, w) \
            and prev_key[0] == y
        if consecutive:
            bad = [p for p in sorted(set(cum) | set(prev_cum))
                   if cum.get(p, 0) - prev_cum.get(p, 0) != weekly.get(p, 0)]
            row["continuity_checked"] = True
            row["continuity_violations"] = len(bad)
            row["continuity_violating_pcodes"] = " ".join(bad)
            row["cum_delta_cases"] = c.sCh.sum() - sum(prev_cum.values())
        else:
            row["continuity_checked"] = False
            row["continuity_violations"] = None
            row["continuity_violating_pcodes"] = ""
            row["cum_delta_cases"] = None
        whov = whok.get(("NGA", tl))
        row["who_week_cases"] = whov
        row["who_minus_ours"] = None if whov is None else whov - row["state_sum_week_cases"]
        row["flag"] = ""
        out.append(row)
        prev_cum, prev_key = cum, (y, w)
    # Sudan: weekly only, no cumulative or national row in the source
    sd = df[df.country == "SDN"]
    for (sw, tl), g in sd.groupby(["source_week", "TL"], sort=True):
        tl = tl.isoformat()
        whov = whok.get(("SDN", tl))
        out.append({"country": "SDN", "source_week": sw, "TL": tl, "states": len(g),
                    "state_sum_week_cases": g.sCh.sum(), "balance": "no national row in source",
                    "continuity_checked": False, "continuity_violating_pcodes": "",
                    "who_week_cases": whov, "who_minus_ours": None if whov is None else whov - g.sCh.sum(),
                    "flag": "source maps this label to the same ISO week as another label"
                    if sw in sdn_meta["labels_not_matching_iso"] or
                    (sd[(sd.TL == g.TL.iloc[0])].source_week.nunique() > 1) else ""})
    cols = ["country", "source_week", "TL", "states", "state_sum_week_cases", "national_week_cases",
            "state_sum_week_deaths", "national_week_deaths", "state_sum_cum_cases", "national_cum_cases",
            "state_sum_cum_deaths", "national_cum_deaths", "balance", "continuity_checked",
            "continuity_violations", "continuity_violating_pcodes", "cum_delta_cases", "who_week_cases",
            "who_minus_ours", "flag"]
    ch = pd.DataFrame(out).reindex(columns=cols)
    ch["source"] = ch.country.map({"NGA": "ncdc", "SDN": "sdn_arcgis"})
    for c in cols[3:12] + ["continuity_violations", "cum_delta_cases", "who_week_cases", "who_minus_ours"]:
        ch[c] = pd.to_numeric(ch[c]).round().astype("Int64")
    ch["continuity_checked"] = ch.continuity_checked.astype(bool)
    return ch.sort_values(["country", "TL", "source_week"], kind="stable").reset_index(drop=True)


def jhu_boundary(tmpl: pd.DataFrame) -> dict:
    """Nigeria weekly suspected cases summed over JHU admin1 rows, last 52 weeks of the release."""
    j = tmpl[tmpl.location_name.str.startswith("AFR::NGA::") & (tmpl.spatial_scale == "admin1")
             & (tmpl.location_name.str.count("::") == 2)]
    g = j.groupby("TL").sCh.sum().sort_index()
    g = g[g.index >= g.index.max() - dt.timedelta(weeks=52)]
    return {"last_TL": g.index.max().isoformat(), "series": [[d.isoformat(), float(v)] for d, v in g.items()]}


# ---------------------------------------------------------------- write
def write_table(df: pd.DataFrame, name: str, out: Path) -> None:
    """CSV plus parquet that keeps pandas metadata (dtypes round-trip for C7); deterministic."""
    out.mkdir(parents=True, exist_ok=True)
    df.to_csv(out / f"{name}.csv", index=False, lineterminator="\n", float_format="%.10g")
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, out / f"{name}.parquet", compression="zstd", write_statistics=False)


def _median_abs(ch: pd.DataFrame, country: str) -> dict:
    d = ch[(ch.country == country) & ch.who_minus_ours.notna()]
    if d.empty:
        return {"weeks_compared": 0}
    ours, who = d.state_sum_week_cases.astype(float), d.who_week_cases.astype(float)
    ratio = (ours / who.where(who > 0)).dropna()
    return {"weeks_compared": int(len(d)), "median_abs_diff": float((who - ours).abs().median()),
            "median_ratio_ours_to_who": float(ratio.median()) if len(ratio) else None,
            "sum_ours": float(ours.sum()), "sum_who": float(who.sum())}


def _cum_delta_vs_who(ch: pd.DataFrame) -> dict:
    """Nigeria: week-on-week change of the printed cumulative vs WHO weekly (consecutive issues only)."""
    d = ch[(ch.country == "NGA") & ch.cum_delta_cases.notna() & ch.who_week_cases.notna()]
    delta, who = d.cum_delta_cases.astype(float), d.who_week_cases.astype(float)
    return {"weeks_compared": int(len(d)), "sum_cum_delta": float(delta.sum()), "sum_who": float(who.sum()),
            "sum_weekly_column": float(d.state_sum_week_cases.astype(float).sum()),
            "median_abs_diff": float((who - delta).abs().median()) if len(d) else None}


def build(raw: Path = RAW, out: Path = OUT_DIR) -> dict:
    snaps = {s: latest_snapshot(f"cholera/{s}", raw) for s in ("ncdc", "sdn_arcgis", "who_adm0", "jhu", "codab")}
    codab = load_codab_admin1(snaps["codab"])
    pidx = pcode_index(codab)
    tmpl = jhu_template(snaps["jhu"])
    nrows, nat, log = ncdc_rows(snaps["ncdc"], pidx)
    srows, smeta = sdn_rows(snaps["sdn_arcgis"], pidx)
    df = to_jhu(nrows + srows, tmpl)
    who = who_weekly(snaps["who_adm0"])
    ch = checks(df, nat, who, smeta)

    out.mkdir(parents=True, exist_ok=True)
    write_table(df, "cholera_admin1", out)
    write_table(ch, "checks", out)
    write_table(who, "who_adm0_weekly", out)

    issues = len(log)
    balanced = sum(x["balanced"] for x in log)
    ng = ch[ch.country == "NGA"]
    report = {
        "snapshots": {k: str(v.relative_to(raw)) for k, v in sorted(snaps.items())},
        "rows": {c: int((df.country == c).sum()) for c in ("NGA", "SDN")},
        "rows_by_obs_type": {f"{c}|{t}": int(n) for (c, t), n in df.groupby(["country", "obs_type"]).size().items()},
        "range": {c: [g.TL.min().isoformat(), g.TR.max().isoformat()] for c, g in df.groupby("country")},
        "ncdc": {"issues": issues, "balanced": balanced, "balance_rate": balanced / issues if issues else None,
                 "exceptions": [{"doc": x["doc"], "national_printed": x["national_printed"], "problems": x["problems"]}
                                for x in log if not x["balanced"]],
                 "issues_log": log},
        "llm_fallback": {"used": False, "docs": 0},
        "sdn": smeta,
        "continuity": {"NGA": {"weeks_checked": int(ng.continuity_checked.sum()),
                               "weeks_with_violations": int((ng.continuity_violations.fillna(0) > 0).sum()),
                               "state_week_violations": int(ng.continuity_violations.fillna(0).sum())},
                       "SDN": {"weeks_checked": 0, "note": "source has weekly counts only"}},
        "who_vs_ours": {c: _median_abs(ch, c) for c in ("NGA", "SDN")},
        "who_vs_ncdc_cum_delta": _cum_delta_vs_who(ch),
        "pcode_coverage": float(df.admin1_pcode.notna().mean()),
        "jhu_boundary": jhu_boundary(tmpl),
        "jhu_columns": list(tmpl.columns),
    }
    report["summary"] = (
        f"NGA {report['rows']['NGA']} rows, SDN {report['rows']['SDN']} rows; NCDC balanced "
        f"{balanced}/{issues}; continuity violations NGA {report['continuity']['NGA']['state_week_violations']} "
        f"state-weeks in {report['continuity']['NGA']['weeks_with_violations']}/"
        f"{report['continuity']['NGA']['weeks_checked']} checked weeks")
    (out / "build_report.json").write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    return report
