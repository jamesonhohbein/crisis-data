"""Spec 001 P2 tests: T11 (no restricted-source value in any published file) and T9 (live site)."""

from __future__ import annotations

import filecmp
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pandas as pd
import pytest
import requests

from crisis_data import publish as pub
from crisis_data.paths import ROOT

RESTRICTED = ("fewsnet", "jmmi")
CANARY_PRICE, CANARY_FX = 7777777.77, 3333333.33


@pytest.fixture(scope="module")
def inputs(built):
    return pub.load_out()


def _bundle(dest: Path, d: dict) -> dict:
    return pub.bundle_from_inputs(dest, pub.load_cfg(), d)


def _scrambled(d: dict) -> dict:
    """Same inputs with every restricted price, rate, market name, id, item and ref replaced."""
    d = dict(d)
    p = d["panel_long"].copy()
    r = p.source.isin(RESTRICTED)
    p.loc[r, "price_local"] = CANARY_PRICE
    p.loc[r, "price_usd"] = CANARY_PRICE
    p.loc[r, "price_per_std_unit"] = CANARY_PRICE
    p.loc[r, "fx_rate"] = CANARY_FX
    p.loc[r, "lat"] = 77.7
    p.loc[r, "lon"] = 77.7
    for c in ("market_name_src", "commodity_src", "raw_ref"):
        p.loc[r, c] = "CANARY"
    p.loc[r, "market_id"] = p.loc[r, "source"] + ":CANARY" + p.loc[r].index.astype(str)
    # a restricted rate in the same place and month as every WFP row: it must still not be used
    fx = d["fx_rates"].copy()
    fx.loc[fx.source.isin(RESTRICTED), "lcu_per_usd"] = CANARY_FX
    d["panel_long"], d["fx_rates"] = p, fx
    return d


def _tables(root: Path):
    for t in sorted(root.rglob("*")):
        if t.suffix == ".parquet":
            yield t, pd.read_parquet(t)
        elif t.suffix == ".csv":
            yield t, pd.read_csv(t, dtype=str, keep_default_na=False)


# ---------------------------------------------------------------- T11
def test_t11_panel_is_public_sources_with_public_rates(inputs, tmp_path):
    rep = _bundle(tmp_path / "b", inputs)
    cfg = pub.load_cfg()
    allowed = {k for k, v in cfg.items() if v.get("license") == "verified" and v.get("panel", True)}
    assert rep["public_sources"] == ["wfp"]
    panel = pd.read_parquet(tmp_path / "b" / "data" / "panel_public.parquet")
    assert set(panel.source) <= allowed and len(panel) > 0
    assert set(panel.fx_source.dropna().str.split(":").str[0]) <= allowed | {"none"}
    # every row without a rate is also without a USD price, and counted
    assert (panel.fx_rate.isna() == panel.price_usd.isna()).all()
    assert rep["panel"]["rows_without_usd"] == int(panel.price_usd.isna().sum())
    assert pub.audit_bundle(tmp_path / "b", cfg) == []


def test_t11_bundle_independent_of_restricted_values(inputs, tmp_path):
    """Replacing every restricted price, rate, market name and id changes no published byte."""
    a, b = tmp_path / "a", tmp_path / "b"
    _bundle(a, inputs)
    _bundle(b, _scrambled(inputs))
    names = sorted(str(p.relative_to(a)) for p in a.rglob("*") if p.is_file())
    assert names == sorted(str(p.relative_to(b)) for p in b.rglob("*") if p.is_file())
    match, mismatch, errors = filecmp.cmpfiles(a, b, names, shallow=False)
    assert not mismatch and not errors, mismatch
    for p in b.rglob("*"):
        if p.is_file() and p.suffix != ".parquet":
            assert "CANARY" not in p.read_text(), p
    for t, df in _tables(b):
        if t.suffix == ".parquet":
            num = df.select_dtypes("number")
            assert not num.isin([CANARY_PRICE, CANARY_FX, 77.7]).any().any(), t
            assert not num.isin([CANARY_PRICE / CANARY_FX]).any().any(), t
        for c in df.columns:
            assert not df[c].astype(str).str.contains("CANARY").any(), (t, c)


def test_t11_no_restricted_market_names(inputs, tmp_path):
    """No market name or market id of a restricted source appears as a value in any published table
    or JSON file (names WFP or COD-AB also use are allowed: they come from those sources)."""
    _bundle(tmp_path / "b", inputs)
    p = inputs["panel_long"]
    geo = json.loads((tmp_path / "b" / "data" / "admin1.geojson").read_text())
    admin = {f["properties"]["adm1_name"] for f in geo["features"]}
    for lay in inputs["codab"].values():  # COD-AB names and pcodes are CC BY-IGO, not source values
        for lvl in ("admin1", "admin2"):
            for f in lay[lvl]["features"]:
                pr = f["properties"]
                admin |= {pr.get(f"{lvl[:3]}{lvl[-1]}_name"), pr.get(f"{lvl[:3]}{lvl[-1]}_pcode")} - {None}
    wfp_names = set(p[p.source == "wfp"].market_name_src.dropna())
    names = set(p[p.source.isin(RESTRICTED)].market_name_src.dropna()) - wfp_names - admin
    ids = set(p[p.source.isin(RESTRICTED)].market_id.dropna())
    ids |= {i.split(":", 1)[1] for i in ids}
    bad = names | (ids - admin)
    assert len(bad) > 100
    for t, df in _tables(tmp_path / "b"):
        for c in df.columns:
            hit = set(df[c].astype(str)) & bad
            assert not hit, (t, c, sorted(hit)[:5])

    def strings(o):
        if isinstance(o, str):
            yield o
        elif isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from strings(v)
        elif isinstance(o, list):
            for v in o:
                yield from strings(v)

    for j in (tmp_path / "b").rglob("*.json"):
        hit = set(strings(json.loads(j.read_text()))) & bad
        assert not hit, (j, sorted(hit)[:5])


def test_t11_coverage_counts_only(inputs, tmp_path):
    _bundle(tmp_path / "b", inputs)
    for t, df in _tables(tmp_path / "b"):
        if set(df.source) & set(RESTRICTED):
            assert set(df.columns) <= pub.COVERAGE_COLS, (t, sorted(set(df.columns) - pub.COVERAGE_COLS))


# ---------------------------------------------------------------- T9 (live site)
T9_URL = os.environ.get("CRISIS_DATA_T9_URL")


@pytest.mark.skipif(not T9_URL, reason="set CRISIS_DATA_T9_URL to the Pages URL")
def test_t9_downloads_match_checksums():
    base = T9_URL.rstrip("/") + "/"
    page = requests.get(base, timeout=30)
    assert page.status_code == 200 and "covmap" not in page.text  # served html, not a 404 page
    listed = dict(re.findall(r'<a href="data/([^"]+)">[^<]+</a></td><td>[^<]*</td><td class="num">[\d,]+</td>'
                             r"<td><code>([0-9a-f]{64})</code>", page.text))
    sums = dict(ln.split()[::-1] for ln in requests.get(base + "data/SHA256SUMS", timeout=30).text.splitlines())
    assert listed and listed == sums
    for name, h in listed.items():
        body = requests.get(base + "data/" + name, timeout=60)
        assert body.status_code == 200, name
        assert hashlib.sha256(body.content).hexdigest() == h, name


@pytest.mark.skipif(not (T9_URL and os.environ.get("NODE_PATH") and shutil.which("node")),
                    reason="needs CRISIS_DATA_T9_URL, node and NODE_PATH with playwright")
def test_t9_map_renders_both_layers():
    shot = os.environ.get("CRISIS_DATA_T9_SHOT", "/tmp/crisis-data-t9.png")
    r = subprocess.run(["node", str(ROOT / "tests" / "t9_render.cjs"), T9_URL, shot],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    res = json.loads(r.stdout.strip().splitlines()[-1])
    assert res["fill_wfp"] > 0 and res["lost"] > 0 and not res["errors"]
