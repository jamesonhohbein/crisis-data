"""Publish: license gate, public bundle, bundle audit, deploy to GitHub Pages.

Rules (spec 001, decision 2026-10-14):
- Only sources with `license: verified` and `panel: true` in sources.yaml reach the public
  panel. A `panel: true` source whose license is not verified makes publish refuse outright.
- Public USD prices are recomputed from the exchange rates of publishable sources only, so a
  restricted source's rate can never shape a published price. Rows left without a rate keep
  `price_usd` empty and are counted in publish_report.json.
- `coverage: true` sources (all three) feed coverage counts: observation counts per source x
  country x admin1 x month and last month seen. No prices, rates or market names.
- After building, `audit_bundle` reads every published file back and refuses if any table
  carrying prices, rates or market names has a row from a non-public source.
- Spec 002: sources marked `cholera: publish` may appear only in tables under cholera/, and only
  with `license: verified` or `license: facts-attribution` plus an `approved` record. Tables
  outside cholera/ keep the rules above unchanged.

`--dry-run` builds and audits the bundle under data/out/public/ and sends nothing.
"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd
import yaml

from crisis_data.build import fx as fxmod
from crisis_data.build.panel import EXTRA_COLS, PANEL_COLS, _write
from crisis_data.paths import OUT, ROOT, SOURCES_YAML, START_MONTH

SITE_SRC = Path(__file__).parent / "site"
PAGES_URL = "https://jamesonhohbein.github.io/crisis-data/"
REPO_URL = "https://github.com/jamesonhohbein/crisis-data"
PRICE_SOURCES = ("wfp", "fewsnet", "jmmi")
SOURCE_LABEL = {"wfp": "WFP", "fewsnet": "FEWS NET", "jmmi": "REACH JMMI"}

# Columns a table may carry when it holds rows from a non-public source: counts and admin units.
COVERAGE_COLS = {
    "source", "country", "admin1_pcode", "admin1_name", "month", "n_pairs", "n_markets",
    "n_staple_pairs", "last_month_seen", "first_month_seen", "months_seen_2024",
    "source_country_last_month", "lost_since_2024", "currency_zone", "n_obs",
}
PUBLIC_PANEL_COLS = PANEL_COLS + EXTRA_COLS
CHOLERA_LICENSES = {"verified", "facts-attribution"}
CHOLERA_DIR = "cholera"
PRICE_LIKE = {"price_local", "price_usd", "price_per_std_unit", "fx_rate", "fx_source", "lcu_per_usd",
              "market_name_src", "market_id", "lat", "lon", "commodity_src", "raw_ref"}


# ---------------------------------------------------------------- config gate
def load_cfg(sources_yaml: Path = SOURCES_YAML) -> dict:
    return yaml.safe_load(sources_yaml.read_text())["sources"]


def gate(cfg: dict) -> list[str]:
    problems = []
    for name, s in sorted(cfg.items()):
        lic = s.get("license")
        if s.get("panel", True) and lic != "verified":
            problems.append(f"{name}: license is '{lic}', not 'verified', and panel is true")
        if lic == "verified" and not (s.get("license_url") and s.get("license_text")):
            problems.append(f"{name}: license 'verified' needs license_url and license_text")
        if s.get("cholera") == "publish":
            if name in PRICE_SOURCES:
                problems.append(f"{name}: a price source cannot be a cholera source")
            if lic not in CHOLERA_LICENSES:
                problems.append(f"{name}: cholera publish needs license in {sorted(CHOLERA_LICENSES)}, got '{lic}'")
            if lic == "facts-attribution" and not (s.get("approved") and s.get("attribution")):
                problems.append(f"{name}: license 'facts-attribution' needs approved and attribution")
    return problems


def cholera_sources(cfg: dict) -> set[str]:
    return {k for k, v in cfg.items() if v.get("cholera") == "publish" and v.get("license") in CHOLERA_LICENSES
            and k not in PRICE_SOURCES}


def public_sources(cfg: dict) -> list[str]:
    return [k for k in PRICE_SOURCES if k in cfg and cfg[k].get("panel", True)
            and cfg[k].get("license") == "verified"]


def coverage_sources(cfg: dict) -> list[str]:
    return [k for k in PRICE_SOURCES if k in cfg and cfg[k].get("coverage")]


# ---------------------------------------------------------------- bundle parts
def public_panel(panel_long: pd.DataFrame, fx: pd.DataFrame, pub: list[str]) -> tuple[pd.DataFrame, dict]:
    """Rows of publishable sources, with FX recomputed from publishable sources' rates only."""
    rows = panel_long[panel_long.source.isin(pub)].copy()
    before = rows.fx_rate.copy()
    rows["market_id_src"] = rows.market_id.str.split(":", n=1).str[1]
    rows = rows.drop(columns=["fx_rate", "fx_source", "price_usd", "price_per_std_unit"])
    rows = fxmod.attach_fx(rows, fx[fx.source.isin(pub)].copy())
    rows["price_usd"] = rows.price_local / rows.fx_rate
    rows["price_per_std_unit"] = rows.price_usd / rows.qty_factor
    same = (rows.fx_rate == before) | (rows.fx_rate.isna() & before.isna())
    nul = rows[rows.price_usd.isna()]
    stats = {
        "rows": int(len(rows)),
        "rows_by_country": {k: int(v) for k, v in rows.groupby("country").size().items()},
        "fx_source": {k: int(v) for k, v in rows.fx_source.fillna("none").value_counts().sort_index().items()},
        "rows_fx_changed_by_public_only_recompute": int((~same).sum()),
        "rows_without_usd": int(len(nul)),
        "rows_without_usd_by_country_month": {f"{c}|{m}": int(n) for (c, m), n in
                                              nul.groupby(["country", "month"]).size().items()},
    }
    out = rows[PUBLIC_PANEL_COLS].sort_values(
        ["country", "source", "market_id", "commodity", "month", "raw_ref"], kind="stable")
    return out.reset_index(drop=True), stats


def public_coverage(cov: pd.DataFrame, summ: pd.DataFrame, cs: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    c = cov[cov.source.isin(cs)][["source", "country", "admin1_pcode", "admin1_name", "month", "n_pairs",
                                  "n_markets", "n_staple_pairs", "last_month_seen"]]
    s = summ[summ.source.isin(cs)][["source", "country", "admin1_pcode", "admin1_name", "first_month_seen",
                                    "last_month_seen", "months_seen_2024", "source_country_last_month",
                                    "lost_since_2024"]]
    return c.reset_index(drop=True), s.reset_index(drop=True)


def yemen_zone_counts(panel_long: pd.DataFrame, cs: list[str]) -> pd.DataFrame:
    """Staple price observations per source x Yemen currency zone x month (counts only)."""
    y = panel_long[(panel_long.country == "YEM") & panel_long.source.isin(cs)]
    g = y.groupby(["source", "currency_zone", "month"]).size().rename("n_obs").reset_index()
    return g.sort_values(["source", "currency_zone", "month"], kind="stable").reset_index(drop=True)


def admin1_geojson(codab: dict, tolerance: float = 0.01) -> dict:
    from shapely.geometry import mapping, shape

    def rnd(o):
        if isinstance(o, float):
            return round(o, 4)
        if isinstance(o, (list, tuple)):
            return [rnd(x) for x in o]
        return o

    feats = []
    for iso in sorted(codab):
        for f in sorted(codab[iso]["admin1"]["features"], key=lambda f: f["properties"]["adm1_pcode"]):
            gm = mapping(shape(f["geometry"]).simplify(tolerance, preserve_topology=True))
            feats.append({"type": "Feature",
                          "properties": {"country": iso, "adm1_pcode": f["properties"]["adm1_pcode"],
                                         "adm1_name": f["properties"]["adm1_name"]},
                          "geometry": {"type": gm["type"], "coordinates": rnd(gm["coordinates"])}})
    return {"type": "FeatureCollection", "features": feats}


def _months(a: str, b: str) -> list[str]:
    out, y, m = [], int(a[:4]), int(a[5:7])
    while f"{y:04d}-{m:02d}" <= b:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def coverage_map(cov: pd.DataFrame, summ: pd.DataFrame, cs: list[str]) -> dict:
    c = cov[cov.admin1_pcode != "UNMATCHED"]
    months = _months(START_MONTH, c[c.n_pairs > 0].month.max())
    counts: dict = {}
    for s in cs:
        d = c[c.source == s].pivot_table(index="admin1_pcode", columns="month", values="n_pairs", aggfunc="sum")
        d = d.reindex(columns=months).fillna(0).astype(int)
        counts[s] = {p: d.loc[p].tolist() for p in sorted(d.index)}
    lost = {s: sorted(summ[(summ.source == s) & summ.lost_since_2024].admin1_pcode.tolist()) for s in cs}
    last = {s: {r.admin1_pcode: r.last_month_seen for r in summ[summ.source == s].itertuples()
                if isinstance(r.last_month_seen, str)} for s in cs}
    return {"months": months, "sources": cs, "labels": {s: SOURCE_LABEL[s] for s in cs},
            "counts": counts, "lost": lost, "last": last}


def lead_finding(zc: pd.DataFrame, years=("2023", "2024", "2025", "2026")) -> dict:
    def by_year(z):
        d = zc[zc.currency_zone == z]
        return {y: int(d[d.month.str[:4] == y].n_obs.sum()) for y in years}

    s = zc[zc.currency_zone == "sanaa"]
    return {"sanaa_by_year": by_year("sanaa"), "aden_by_year": by_year("aden"),
            "sanaa_last_month": {src: g.month.max() for src, g in s.groupby("source")}}


# ---------------------------------------------------------------- audit
def audit_bundle(root: Path, cfg: dict) -> list[str]:
    """Read every published table back. A table with rows from a non-public source may only carry
    COVERAGE_COLS; exchange rates must come from public sources."""
    # Computed here, not via public_sources(), so the audit stays independent of the selection code.
    pub = {k for k, v in cfg.items() if v.get("license") == "verified" and v.get("panel", True)}
    chol = cholera_sources(cfg)
    problems = []
    for t in sorted(root.rglob("*.parquet")) + sorted(root.rglob("*.csv")):
        df = pd.read_parquet(t) if t.suffix == ".parquet" else pd.read_csv(t, dtype=str, keep_default_na=False)
        rel = t.relative_to(root)
        cols = set(df.columns)
        if rel.parts[0] == CHOLERA_DIR:
            # spec 002 tables: every row from a cholera-publishable source, no price-like columns
            if "source" not in cols:
                problems.append(f"{rel}: cholera table without a source column")
            elif set(df.source.unique()) - chol:
                problems.append(f"{rel}: rows from {sorted(set(df.source.unique()) - chol)} under cholera/")
            if cols & PRICE_LIKE:
                problems.append(f"{rel}: price or market columns under cholera/")
            continue
        if "source" not in cols:
            if cols & PRICE_LIKE:
                problems.append(f"{rel}: price or market columns without a source column")
            continue
        foreign = set(df.source.unique()) - pub
        if foreign and not cols <= COVERAGE_COLS:
            problems.append(f"{rel}: rows from {sorted(foreign)} with columns {sorted(cols - COVERAGE_COLS)}")
        if "fx_source" in cols:
            pre = set(df.fx_source.dropna().astype(str).str.split(":").str[0]) - {"", "none"}
            if pre - pub:
                problems.append(f"{rel}: exchange rates from {sorted(pre - pub)}")
    cm = root / "coverage_map.json"
    if cm.exists():
        extra = set(json.loads(cm.read_text())) - {"months", "sources", "labels", "counts", "lost", "last"}
        if extra:
            problems.append(f"coverage_map.json: unexpected keys {sorted(extra)}")
    return problems


# ---------------------------------------------------------------- site
def _sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _fmt(n: int) -> str:
    return f"{n:,}"


def render_index(ctx: dict) -> str:
    t = (SITE_SRC / "index.html").read_text()
    for k, v in ctx.items():
        t = t.replace("{{" + k + "}}", v)
    if "{{" in t:
        raise ValueError("unfilled placeholder in index.html")
    return t


FILE_LABELS = {
    "panel_public": "WFP price panel, one row per observation",
    "coverage": "Coverage counts, source x admin1 x month (all three sources)",
    "coverage_summary": "Coverage summary: first and last month seen, lost since 2024",
    "yemen_zone_counts": "Yemen observation counts by currency zone and month",
    "admin1": "Admin1 boundaries (OCHA COD-AB, simplified)",
    "ATTRIBUTION": "Attribution", "LICENSE-DATA": "Data license (CC BY 4.0)",
}


def build_bundle(dest: Path, cfg: dict, panel_long: pd.DataFrame, fx: pd.DataFrame, cov: pd.DataFrame,
                 summ: pd.DataFrame, codab: dict, build_report: dict, cholera_out: Path | None = None) -> dict:
    """Write the public bundle (site + data) to dest. Returns the publish report."""
    pub, cs = public_sources(cfg), coverage_sources(cfg)
    if dest.exists():
        shutil.rmtree(dest)
    data = dest / "data"
    data.mkdir(parents=True)

    panel, pstats = public_panel(panel_long, fx, pub)
    c, s = public_coverage(cov, summ, cs)
    zc = yemen_zone_counts(panel_long, cs)
    _write(panel, "panel_public", data)
    _write(c, "coverage", data)
    _write(s, "coverage_summary", data)
    _write(zc, "yemen_zone_counts", data)
    (data / "admin1.geojson").write_text(json.dumps(admin1_geojson(codab), separators=(",", ":"),
                                                    sort_keys=True) + "\n")
    cmap = coverage_map(c, s, cs)
    (dest / "coverage_map.json").write_text(json.dumps(cmap, separators=(",", ":"), sort_keys=True) + "\n")
    for f in ("app.js", "style.css"):
        shutil.copyfile(SITE_SRC / f, dest / f)
    for f in ("ATTRIBUTION.md", "LICENSE-DATA"):
        shutil.copyfile(ROOT / f, data / f)
    (dest / ".nojekyll").write_text("")

    files = sorted(p for p in data.iterdir() if p.is_file())
    sums = {p.name: _sha256(p) for p in files}
    (data / "SHA256SUMS").write_text("".join(f"{h}  {n}\n" for n, h in sums.items()))

    lead = lead_finding(zc)
    snaps = build_report.get("snapshots", {})
    report = {
        "public_sources": pub, "coverage_sources": cs, "panel": pstats,
        "coverage_rows": int(len(c)), "coverage_summary_rows": int(len(s)), "yemen_zone_count_rows": int(len(zc)),
        "lead": lead, "snapshots": snaps, "sha256": sums,
    }

    sy, sl, ay = lead["sanaa_by_year"], lead["sanaa_last_month"], lead["aden_by_year"]
    lead_html = (
        "<p class=\"lead\">Staple price observations from Sana'a-governed Yemen, all three sources "
        f"counted: <b>{_fmt(sy['2023'])}</b> in 2023, <b>{_fmt(sy['2024'])}</b> in 2024, "
        f"<b>{_fmt(sy['2025'])}</b> in 2025, <b>{_fmt(sy['2026'])}</b> in 2026. Last month with a row: "
        + ", ".join(f"{SOURCE_LABEL[k]} {html.escape(sl[k])}" for k in sorted(sl, key=lambda k: sl[k]))
        + f". Aden-governed Yemen over the same years: {_fmt(ay['2023'])}, {_fmt(ay['2024'])}, "
        f"{_fmt(ay['2025'])}, {_fmt(ay['2026'])}.</p>")
    rows = [f"<tr><td><a href=\"data/{p.name}\">{p.name}</a></td>"
            f"<td>{html.escape(FILE_LABELS.get(p.name.split('.')[0], ''))}</td>"
            f"<td class=\"num\">{p.stat().st_size:,}</td><td><code>{sums[p.name]}</code></td></tr>" for p in files]
    rows.append("<tr><td><a href=\"data/SHA256SUMS\">SHA256SUMS</a></td><td>Checksums of the files above</td>"
                f"<td class=\"num\">{(data / 'SHA256SUMS').stat().st_size:,}</td><td></td></tr>")
    ctx = {
        "LEAD": lead_html,
        "DOWNLOADS": "\n".join(rows),
        "PANEL_ROWS": _fmt(pstats["rows"]),
        "PANEL_SDN": _fmt(pstats["rows_by_country"].get("SDN", 0)),
        "PANEL_YEM": _fmt(pstats["rows_by_country"].get("YEM", 0)),
        "NULL_USD": _fmt(pstats["rows_without_usd"]),
        "SNAPSHOTS": html.escape(", ".join(f"{k} {v.split('/')[-1]}" for k, v in sorted(snaps.items()))),
        "LAST_MONTH": html.escape(cmap["months"][-1]),
        "REPO_URL": REPO_URL,
        "CHOLERA_LINK": ("<nav class=\"small\">Also on this site: <a href=\"cholera/\">subnational cholera, "
                         "Nigeria and Sudan, 2024-25</a></nav>") if cholera_out is not None else "",
    }
    if cholera_out is not None:
        from crisis_data.cholera.site import bundle as cholera_bundle

        report["cholera"] = cholera_bundle(dest / CHOLERA_DIR, cholera_out)
    (dest / "index.html").write_text(render_index(ctx))
    (dest / "publish_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def load_out(out: Path = OUT) -> dict:
    from crisis_data.build.load import load_codab
    from crisis_data.paths import latest_snapshot

    need = ["panel_long", "fx_rates", "coverage", "coverage_summary"]
    missing = [n for n in need if not (out / f"{n}.parquet").exists()]
    if missing:
        raise FileNotFoundError(f"{missing} missing in {out}: run `crisis-data build` first")
    d = {n: pd.read_parquet(out / f"{n}.parquet") for n in need}
    d["codab"] = load_codab(latest_snapshot("codab"))
    d["build_report"] = json.loads((out / "build_report.json").read_text())
    d["cholera"] = out / CHOLERA_DIR if (out / CHOLERA_DIR / "build_report.json").exists() else None
    return d


def bundle_from_inputs(dest: Path, cfg: dict, d: dict) -> dict:
    return build_bundle(dest, cfg, d["panel_long"], d["fx_rates"], d["coverage"], d["coverage_summary"],
                        d["codab"], d["build_report"], d.get("cholera"))


# ---------------------------------------------------------------- deploy
def _git(args: list[str], cwd: Path | None = None, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, **kw)


def deploy(bundle: Path, branch: str = "gh-pages", remote: str | None = None) -> str:
    """Commit the bundle to `branch` of the origin remote from a temporary clone, then push.

    Returns "unchanged" without committing when the bundle equals the branch tip, so a rerun on the
    same inputs makes no new commit (P3 refresh)."""
    remote = remote or _git(["remote", "get-url", "origin"], ROOT, capture_output=True, text=True,
                            check=True).stdout.strip()
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "pages"
        if _git(["ls-remote", "--exit-code", "--heads", remote, branch], capture_output=True).returncode == 0:
            _git(["clone", "--quiet", "--depth", "1", "--branch", branch, remote, str(w)], check=True)
        else:
            _git(["init", "--quiet", "--initial-branch", branch, str(w)], check=True)
            _git(["remote", "add", "origin", remote], w, check=True)
        for p in w.iterdir():
            if p.name != ".git":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
        shutil.copytree(bundle, w, dirs_exist_ok=True)
        _git(["add", "--all", "."], w, check=True)
        if _git(["diff", "--cached", "--quiet"], w).returncode == 0:
            return "unchanged"
        tag = _sha256(bundle / "data" / "SHA256SUMS")[:12]
        _git(["commit", "--quiet", "-m", f"Publish site and data bundle {tag}"], w, check=True)
        _git(["push", "--quiet", "origin", f"HEAD:{branch}"], w, check=True)
        return tag


# ---------------------------------------------------------------- entry
def main(dry_run: bool, sources_yaml: Path = SOURCES_YAML, out: Path = OUT, dest: Path | None = None) -> int:
    cfg = load_cfg(sources_yaml)
    dest = dest or out / "public"
    problems = gate(cfg)
    report = None
    if not problems:
        try:
            report = bundle_from_inputs(dest, cfg, load_out(out))
        except FileNotFoundError as e:
            problems.append(str(e))
        else:
            problems += audit_bundle(dest, cfg)
    for p in problems:
        print(f"publish gate: {p}", file=sys.stderr)
    if problems:
        if dest.exists():
            shutil.rmtree(dest)
        print("publish refused", file=sys.stderr)
        return 1
    p = report["panel"]
    print(f"bundle {dest}: panel {p['rows']} rows ({', '.join(report['public_sources'])}), "
          f"{p['rows_without_usd']} without USD; coverage {report['coverage_rows']} rows "
          f"({', '.join(report['coverage_sources'])})")
    if "cholera" in report:
        print(f"cholera/: rows {report['cholera']['rows']}")
    if dry_run:
        print("publish gate passed (dry run, nothing sent)")
        return 0
    print(f"deployed gh-pages: {deploy(dest)}")
    return 0
