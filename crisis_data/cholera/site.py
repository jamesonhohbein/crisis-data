"""The /cholera/ page of the public bundle (spec 002 P2).

Written into <bundle>/cholera/ by crisis_data.publish.build_bundle, so one gh-pages deploy carries
both pages. Every table here holds rows only from `cholera: publish` sources (checked by
publish.audit_bundle).
"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
from pathlib import Path

import pandas as pd

from crisis_data.cholera.build import load_codab_admin1
from crisis_data.paths import RAW, ROOT, latest_snapshot

SITE = Path(__file__).parent / "site"
NAMES = {"NGA": "Nigeria", "SDN": "Sudan"}
BOUNDS = {"NGA": [[2.6, 4.2], [14.7, 13.9]], "SDN": [[21.8, 8.6], [38.7, 22.3]]}
FILES = ("cholera_admin1", "checks", "who_adm0_weekly")
LABELS = {
    "cholera_admin1": "Admin1 rows in the JHU schema plus source_doc, vintage, method, current",
    "checks": "Per country and week: state sum vs national, cumulative continuity, WHO comparison",
    "who_adm0_weekly": "WHO national weekly cases and deaths (reference only)",
    "admin1": "Admin1 boundaries, Nigeria and Sudan (OCHA COD-AB, simplified)",
    "build_report": "Build report: issues parsed, exceptions, measured check results",
    "ATTRIBUTION": "Attribution", "LICENSE-DATA": "Data license (CC BY 4.0)",
}


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _f(x) -> float | None:
    return None if pd.isna(x) else float(x)


def series(df: pd.DataFrame, ch: pd.DataFrame, who: pd.DataFrame, report: dict) -> dict:
    out: dict = {"countries": {}, "boundary": {}}
    for c in ("NGA", "SDN"):
        d = df[df.country == c]
        k = ch[ch.country == c]
        weeks = k.source_week.tolist()
        tl = k.TL.tolist()
        meas = {}
        for t in sorted(d.obs_type.unique()):
            p = d[d.obs_type == t].pivot_table(index="admin1_pcode", columns="source_week", values="sCh",
                                               aggfunc="sum")
            p = p.reindex(columns=weeks)
            meas[t] = {pc: [_f(v) for v in p.loc[pc].tolist()] for pc in sorted(p.index)}
        w = who[(who.country == c) & (who.TL >= min(tl)) & (who.TL <= max(tl))]
        out["countries"][c] = {
            "name": NAMES[c], "bounds": BOUNDS[c], "weeks": weeks, "TL": tl, "measures": meas,
            "ours": [_f(v) for v in k.state_sum_week_cases.tolist()],
            "cum_delta": [_f(v) for v in k.cum_delta_cases.tolist()],
            "who": [[r.TL, _f(r.cases)] for r in w.itertuples()],
        }
    b = report["jhu_boundary"]
    ng = ch[ch.country == "NGA"]
    out["boundary"] = {"jhu": b["series"], "ours": [[t, _f(v)] for t, v in zip(ng.TL, ng.state_sum_week_cases)],
                       "who": [[r.TL, _f(r.cases)] for r in who[(who.country == "NGA")].itertuples()
                               if r.TL <= "2024-12-31"]}
    return out


def lead(report: dict, ch: pd.DataFrame) -> str:
    cd = report["who_vs_ncdc_cum_delta"]
    s = ch[(ch.country == "SDN") & ch.who_week_cases.notna()]
    same = int((s.who_minus_ours == 0).sum())
    close = int(((s.who_minus_ours.abs() / s.who_week_cases) <= 0.01).sum())
    ng = ch[(ch.country == "NGA") & ch.continuity_checked]
    w0, w1 = ng.source_week.min(), ng.source_week.max()
    return (
        "<p class=\"lead\">Nigeria's state tables undercount by week, not by year: over the "
        f"{cd['weeks_compared']} consecutive weekly sitreps {w0} to {w1}, the \"current week\" column "
        f"summed to <b>{cd['sum_weekly_column']:,.0f}</b> suspected cases while the same tables' "
        f"year-to-date total rose by <b>{cd['sum_cum_delta']:,.0f}</b> and WHO's national weekly series "
        f"counted <b>{cd['sum_who']:,.0f}</b>. Late reports land in the cumulative column, so the weekly "
        f"column alone misses about {1 - cd['sum_weekly_column'] / cd['sum_cum_delta']:.0%} of cases. "
        f"Sudan's state layer is within 1% of WHO's national weekly count in {close} of {len(s)} weeks "
        f"(exactly equal in {same}).</p>")


def bundle(dest: Path, cholera_out: Path, raw: Path = RAW) -> dict:
    """Write the /cholera/ page and its data under dest. Returns a small report."""
    if dest.exists():
        shutil.rmtree(dest)
    data = dest / "data"
    data.mkdir(parents=True)
    df = pd.read_parquet(cholera_out / "cholera_admin1.parquet")
    ch = pd.read_parquet(cholera_out / "checks.parquet")
    who = pd.read_parquet(cholera_out / "who_adm0_weekly.parquet")
    report = json.loads((cholera_out / "build_report.json").read_text())
    for n in FILES:
        for ext in ("csv", "parquet"):
            shutil.copyfile(cholera_out / f"{n}.{ext}", data / f"{n}.{ext}")
    pub_report = {k: v for k, v in report.items() if k not in ("jhu_boundary",)}
    (data / "build_report.json").write_text(json.dumps(pub_report, indent=2, sort_keys=True) + "\n")

    from crisis_data.publish import admin1_geojson

    codab = {iso: {"admin1": g} for iso, g in load_codab_admin1(latest_snapshot("cholera/codab", raw)).items()}
    (data / "admin1.geojson").write_text(json.dumps(admin1_geojson(codab), separators=(",", ":"),
                                                    sort_keys=True) + "\n")
    (dest / "series.json").write_text(json.dumps(series(df, ch, who, report), separators=(",", ":"),
                                                 sort_keys=True) + "\n")
    shutil.copyfile(ROOT / "ATTRIBUTION-cholera.md", data / "ATTRIBUTION.md")
    shutil.copyfile(ROOT / "LICENSE-DATA", data / "LICENSE-DATA")
    shutil.copyfile(SITE / "app.js", dest / "app.js")

    files = sorted(p for p in data.iterdir() if p.is_file())
    sums = {p.name: _sha(p) for p in files}
    (data / "SHA256SUMS").write_text("".join(f"{h}  {n}\n" for n, h in sums.items()))
    rows = [f"<tr><td><a href=\"data/{p.name}\">{p.name}</a></td>"
            f"<td>{html.escape(LABELS.get(p.name.split('.')[0], ''))}</td>"
            f"<td class=\"num\">{p.stat().st_size:,}</td><td><code>{sums[p.name]}</code></td></tr>" for p in files]
    rows.append("<tr><td><a href=\"data/SHA256SUMS\">SHA256SUMS</a></td><td>Checksums of the files above</td>"
                f"<td class=\"num\">{(data / 'SHA256SUMS').stat().st_size:,}</td><td></td></tr>")

    n = report["ncdc"]
    ex = "; ".join(f"{e['doc']} ({', '.join(e['problems']) or 'unbalanced'})" for e in n["exceptions"]) or "none"
    cont = report["continuity"]["NGA"]
    wv = report["who_vs_ours"]
    cd = report["who_vs_ncdc_cum_delta"]
    ctx = {
        "LEAD": lead(report, ch),
        "DOWNLOADS": "\n".join(rows),
        "NGA_ROWS": f"{report['rows']['NGA']:,}", "SDN_ROWS": f"{report['rows']['SDN']:,}",
        "NGA_RANGE": " to ".join(report["range"]["NGA"]), "SDN_RANGE": " to ".join(report["range"]["SDN"]),
        "ISSUES": str(n["issues"]), "BALANCED": str(n["balanced"]), "EXCEPTIONS": html.escape(ex),
        "CONT_WEEKS": str(cont["weeks_checked"]), "CONT_BAD_WEEKS": str(cont["weeks_with_violations"]),
        "CONT_BAD": str(cont["state_week_violations"]),
        "NGA_MAD": f"{wv['NGA']['median_abs_diff']:,.0f}", "NGA_RATIO": f"{wv['NGA']['median_ratio_ours_to_who']:.2f}",
        "SDN_MAD": f"{wv['SDN']['median_abs_diff']:,.0f}",
        "NGA_WEEKLY_SHARE_CUM": f"{cd['sum_weekly_column'] / cd['sum_cum_delta']:.0%}",
        "NGA_WEEKLY_SHARE_WHO": f"{cd['sum_weekly_column'] / cd['sum_who']:.0%}",
        "JHU_LAST": html.escape(report["jhu_boundary"]["last_TL"]),
        "SNAPSHOTS": html.escape(", ".join(f"{k} {v.split('/')[-1]}" for k, v in sorted(report["snapshots"].items()))),
    }
    t = (SITE / "index.html").read_text()
    for k, v in ctx.items():
        t = t.replace("{{" + k + "}}", v)
    if "{{" in t:
        raise ValueError("unfilled placeholder in cholera/index.html")
    (dest / "index.html").write_text(t)
    return {"sha256": sums, "rows": report["rows"], "balanced": n["balanced"], "issues": n["issues"]}
