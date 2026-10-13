"""Spec 001 P1 tests, T1-T8. T5 is a recorded hand check (tests/T5_handcheck.md)."""

from __future__ import annotations

import csv
import filecmp
import re
import subprocess
from pathlib import Path

import pandas as pd
import pytest
import yaml

from crisis_data.build import fx as fxmod
from crisis_data.fetch.common import verify_manifest
from crisis_data.paths import REF, ROOT, latest_snapshot
from tests.conftest import needs_raw

HERE = Path(__file__).parent


# ---------------------------------------------------------------- T1
@needs_raw
@pytest.mark.parametrize("source", ["wfp", "fewsnet", "jmmi"])
def test_t1_fetchers_have_2024_2026_rows_and_manifest(source):
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())["sources"][source]
    snap = latest_snapshot(source)
    assert verify_manifest(snap) == [], f"{source} manifest does not verify"
    from crisis_data.build import jmmi_load, load

    drops: dict = {}
    if source == "wfp":
        obs, _ = load.load_wfp(snap, drops)
    elif source == "fewsnet":
        obs, _ = load.load_fewsnet(snap, drops)
    else:
        obs, _ = jmmi_load.load_jmmi(snap, drops)
    expected = cfg.get("expected_countries", ["SDN", "YEM"])
    for iso3 in expected:
        years = set(obs[obs.country == iso3].month.str[:4])
        for y in ("2024", "2025", "2026"):
            if y in cfg.get("known_gaps", {}).get(iso3, []):
                continue
            assert y in years, f"{source} {iso3} has no {y} rows"
    missing = set(["SDN", "YEM"]) - set(expected)
    for iso3 in missing:  # a country with no machine-readable data must be documented, not silent
        assert cfg.get("unavailable", {}).get(iso3), f"{source} {iso3} missing without an `unavailable` note"


# ---------------------------------------------------------------- T2
def test_t2_units_normalized_and_drops_counted(built):
    p, rep = built["panel"], built["report"]
    assert len(p) > 0
    assert set(p.std_unit.unique()) <= {"kg", "L", "each"}
    units = pd.read_csv(REF / "units.csv", keep_default_na=False)
    u = units[units.std_unit != ""].set_index(["source", "unit_src"])
    keyed = p.set_index(["source", "unit_src"])
    ref_factor = u.qty_factor.astype(float).reindex(keyed.index).to_numpy()
    assert (keyed.qty_factor.to_numpy() == ref_factor).all()
    assert rep["drops"], "build_report.json has no drop counts"
    assert all(isinstance(v, int) and v > 0 for v in rep["drops"].values())
    assert any(k.endswith(":commodity_unmapped") for k in rep["drops"])


# ---------------------------------------------------------------- T3
def test_t3_fixture_aden_and_sanaa_convert_with_different_rates():
    rows = pd.DataFrame([
        dict(source="fewsnet", country="YEM", market_id_src="A", admin1_pcode="YE24", currency_zone="aden",
             month="2025-03", currency="YER", price_local=2200.0),
        dict(source="fewsnet", country="YEM", market_id_src="S", admin1_pcode="YE13", currency_zone="sanaa",
             month="2025-03", currency="YER", price_local=2200.0),
    ])
    fx = pd.DataFrame([
        dict(source="wfp", country="YEM", market_id_src="w1", admin1_pcode="YE24", currency_zone="aden",
             month="2025-03", lcu_per_usd=2200.0),
        dict(source="wfp", country="YEM", market_id_src="w2", admin1_pcode="YE13", currency_zone="sanaa",
             month="2025-03", lcu_per_usd=535.0),
    ])
    out = fxmod.attach_fx(rows, fx).set_index("market_id_src")
    assert out.loc["A", "fx_rate"] == 2200.0 and out.loc["S", "fx_rate"] == 535.0
    assert out.loc["A", "fx_source"] == "wfp:admin1"
    # zone fallback never crosses zones: drop the Sana'a admin1 rate and the row must stay unconverted
    out2 = fxmod.attach_fx(rows, fx[fx.currency_zone == "aden"]).set_index("market_id_src")
    assert pd.isna(out2.loc["S", "fx_rate"])


def test_t3_every_yemen_row_has_zone(built):
    p = built["panel"]
    y = p[p.country == "YEM"]
    assert len(y) > 0
    assert y.currency_zone.isin(["aden", "sanaa"]).all()
    # a converted Aden row and Sana'a row in the same month use different rates
    m = y.dropna(subset=["fx_rate"]).groupby(["month", "currency_zone"]).fx_rate.median().unstack()
    m = m.dropna()
    assert len(m) > 0 and (m.aden / m.sanaa > 1.5).all()


def test_t3_zone_map_agrees_with_reported_rates(built):
    zc = built["report"]["fx_zone_check"]["rates_applied_to_panel"]
    assert zc["rates_checked"] > 0
    assert zc["rates_on_wrong_side_of_zone_midpoint"] == 0, zc


# ---------------------------------------------------------------- T4
def test_t4_admin_match_rates_and_review_file(built):
    # Spec target: 95% admin1, 85% admin2. Area averages (FEWS NET Yemen "Governorate average"
    # series) are admin1-level by construction: no district exists to match. The admin2 target
    # is asserted on point markets; the all-rows figure is printed and reported, not hidden.
    allp = built["panel"]
    a1 = allp.admin1_pcode.notna().mean()
    a2_all = allp.admin2_pcode.notna().mean()
    p = allp[allp.match_method != "area_average"]
    a2 = p.admin2_pcode.notna().mean()
    print(f"admin1 {a1:.4f} admin2 all rows {a2_all:.4f} admin2 point markets {a2:.4f} "
          f"area-average rows {len(allp) - len(p)}")
    assert a1 >= 0.95, a1
    assert a2 >= 0.85, a2
    review = pd.read_csv(REF / "markets_review.csv", dtype=str, keep_default_na=False)
    listed = set(zip(review.source, review.country, review.market_id_src))
    un = p[p.admin2_pcode.isna()]
    for r in un.drop_duplicates("market_id").itertuples():
        src_id = r.market_id.split(":", 1)[1]
        assert (r.source, r.country, src_id) in listed, f"unmatched {r.market_id} not in review file"


# ---------------------------------------------------------------- T5
def test_t5_handcheck_recorded():
    text = (HERE / "T5_handcheck.md").read_text()
    rows = [ln for ln in text.splitlines() if re.match(r"^\|\s*\d+\s*\|", ln)]
    assert len(rows) == 20, len(rows)
    for ln in rows:
        cells = [c.strip() for c in ln.strip("|").split("|")]
        assert cells[-1].lower() == "yes", ln


# ---------------------------------------------------------------- T6
def _raw_max_month(source: str, iso3: str) -> str:
    snap = latest_snapshot(source)
    best = ""
    if source == "wfp":
        with open(snap / f"wfp_food_prices_{iso3.lower()}.csv", newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r["price"]:
                    best = max(best, r["date"][:7])
    elif source == "fewsnet":
        for p in (snap / "marketpricefacts" / iso3.lower()).glob("*.csv"):
            with open(p, newline="", encoding="utf-8-sig") as f:
                for r in csv.DictReader(f):
                    if r["status"] == "Collected" and r["value"]:
                        best = max(best, r["period_date"][:7])
    return best


@needs_raw
@pytest.mark.parametrize("source", ["wfp", "fewsnet"])
@pytest.mark.parametrize("iso3", ["SDN", "YEM"])
def test_t6_coverage_last_seen_equals_raw_max(built, source, iso3):
    cov = built["coverage"]
    c = cov[(cov.source == source) & (cov.country == iso3) & (cov.n_pairs > 0)]
    assert c.last_month_seen.max() == _raw_max_month(source, iso3)
    assert built["report"]["last_month_seen"][f"{source}|{iso3}"] == _raw_max_month(source, iso3)


@needs_raw
def test_t6_jmmi_coverage_last_seen(built):
    from crisis_data.build import jmmi_load

    try:
        snap = latest_snapshot("jmmi")
    except FileNotFoundError:
        pytest.skip("no jmmi snapshot")
    obs, _ = jmmi_load.load_jmmi(snap, {})
    cov = built["coverage"]
    for iso3, g in obs.groupby("country"):
        c = cov[(cov.source == "jmmi") & (cov.country == iso3) & (cov.n_pairs > 0)]
        assert c.last_month_seen.max() == jmmi_load.raw_max_month(snap, iso3)


# ---------------------------------------------------------------- T7
def _run_publish(sources_yaml: Path) -> int:
    from crisis_data.publish import main

    return main(dry_run=True, sources_yaml=sources_yaml)


def test_t7_publish_refuses_unverified_in_panel(built, tmp_path):
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    unverified = [k for k, v in cfg["sources"].items() if v.get("license") != "verified"]
    present = set(built["panel"].source.unique())
    if set(unverified) & present:
        assert _run_publish(ROOT / "sources.yaml") != 0
    # forced case: mark wfp unverified
    cfg2 = yaml.safe_load((ROOT / "sources.yaml").read_text())
    cfg2["sources"]["wfp"]["license"] = "unverified"
    f = tmp_path / "s.yaml"
    f.write_text(yaml.safe_dump(cfg2))
    assert _run_publish(f) != 0
    # same unverified sources moved to coverage-only: gate passes
    cfg3 = yaml.safe_load((ROOT / "sources.yaml").read_text())
    for k in unverified:
        cfg3["sources"][k]["panel"] = False
    f3 = tmp_path / "s3.yaml"
    f3.write_text(yaml.safe_dump(cfg3))
    assert _run_publish(f3) == 0


def test_t7_cli_exit_code(built):
    r = subprocess.run(["uv", "run", "--quiet", "crisis-data", "publish", "--dry-run"], cwd=ROOT,
                       capture_output=True, text=True)
    cfg = yaml.safe_load((ROOT / "sources.yaml").read_text())
    any_unverified = any(v.get("license") != "verified" and v.get("panel", True) and k in set(built["panel"].source)
                         for k, v in cfg["sources"].items())
    assert (r.returncode != 0) == any_unverified, r.stderr


# ---------------------------------------------------------------- T8
@needs_raw
def test_t8_byte_identical_outputs(tmp_path):
    from crisis_data.build.panel import build

    a, b = tmp_path / "a", tmp_path / "b"
    build(out=a, write_review=False)
    build(out=b, write_review=False)
    names = sorted(p.name for p in a.iterdir())
    assert names == sorted(p.name for p in b.iterdir()) and names
    match, mismatch, errors = filecmp.cmpfiles(a, b, names, shallow=False)
    assert not mismatch and not errors, mismatch
