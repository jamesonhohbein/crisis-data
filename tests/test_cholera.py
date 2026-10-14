"""Spec 002 tests C1-C9 (subnational cholera). Need the raw snapshots under data/raw/cholera/."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request

import pandas as pd
import pytest

from crisis_data.cholera.build import build, jhu_template, load_codab_admin1
from crisis_data.cholera.fetch import NCDC_FIRST, NCDC_LAST, ncdc_listing
from crisis_data.fetch.common import verify_manifest
from crisis_data.paths import RAW, REF, ROOT, latest_snapshot

SRC = ("ncdc", "sdn_arcgis", "who_adm0", "jhu", "codab")


def _have() -> bool:
    return all(any((RAW / "cholera" / s).glob("????-??-??/MANIFEST.sha256")) for s in SRC)


pytestmark = pytest.mark.skipif(not _have(), reason="no cholera snapshot; run `crisis-data cholera fetch`")


@pytest.fixture(scope="module")
def out(tmp_path_factory):
    d = tmp_path_factory.mktemp("cholera_a")
    rep = build(out=d)
    return {"dir": d, "report": rep, "rows": pd.read_parquet(d / "cholera_admin1.parquet"),
            "checks": pd.read_parquet(d / "checks.parquet")}


def test_c1_fetch_complete():
    snap = latest_snapshot("cholera/ncdc")
    assert verify_manifest(snap) == []
    meta = json.loads((snap / "fetch_meta.json").read_text())
    listed = [x for x in ncdc_listing((snap / "listing.html").read_text())
              if NCDC_FIRST <= (x["year"], x["week"]) <= NCDC_LAST]
    assert len(listed) == meta["listed_in_scope"] == len(meta["files"]) == 24
    assert sorted(p.name for p in snap.glob("*.pdf")) == sorted(meta["files"])
    s = latest_snapshot("cholera/sdn_arcgis")
    assert verify_manifest(s) == []
    sm = json.loads((s / "fetch_meta.json").read_text())
    feats = json.loads((s / "features.json").read_text())
    assert sm["return_count_only"] == sm["features"] == len(feats)


def test_c2_balance(out):
    n = out["report"]["ncdc"]
    printed = [x for x in n["issues_log"] if x["national_printed"]]
    # Amended target (spec status): every issue that prints a national row balances; the one issue
    # without a printed national total (2024 W30, #N/A in the source) is listed as an exception.
    assert all(x["balanced"] for x in printed)
    assert n["balanced"] == len(printed) == 23 and n["issues"] == 24
    assert [e["doc"] for e in n["exceptions"]] == ["ncdc_2024_w30.pdf"]
    assert out["report"]["llm_fallback"] == {"used": False, "docs": 0}
    assert set(out["rows"].method) == {"parse", "api"}


def test_c3_continuity_counted(out):
    ch = out["checks"]
    ng = ch[ch.country == "NGA"]
    c = out["report"]["continuity"]["NGA"]
    assert int(ng.continuity_checked.sum()) == c["weeks_checked"] > 0
    assert int(ng.continuity_violations.fillna(0).sum()) == c["state_week_violations"]
    # values as reported: the cumulative rows equal the parsed national sums, nothing adjusted
    cu = out["rows"][(out["rows"].country == "NGA") & (out["rows"].obs_type == "cumulative")]
    sums = cu.groupby("source_week").sCh.sum()
    for x in out["report"]["ncdc"]["issues_log"]:
        assert sums[f"{x['year']}-W{x['week']:02d}"] == x["state_sums"]["cum_cases"]


def test_c4_who_comparison(out):
    w = out["report"]["who_vs_ours"]
    for c in ("NGA", "SDN"):
        assert w[c]["weeks_compared"] > 0 and w[c]["median_abs_diff"] >= 0
    assert out["checks"].who_week_cases.notna().sum() >= w["NGA"]["weeks_compared"] + w["SDN"]["weeks_compared"]


def test_c5_pcodes(out):
    r = out["rows"]
    codab = load_codab_admin1(latest_snapshot("cholera/codab"))
    valid = {f["properties"]["adm1_pcode"] for g in codab.values() for f in g["features"]}
    assert r.admin1_pcode.notna().all() and set(r.admin1_pcode) <= valid


def test_c6_handcheck(out):
    rows = list(csv.DictReader((REF / "cholera_handcheck.csv").open()))
    assert len(rows) == 20 and all(x["verdict"] == "match" for x in rows)
    r = out["rows"]
    for x in rows:
        m = r[(r.admin1_pcode == x["admin1_pcode"]) & (r.obs_type == x["obs_type"])
              & (r.source_week == x["source_week"]) & (r.source_doc == x["source_doc"])]
        assert len(m) == 1, x
        assert m.sCh.iloc[0] == float(x["sCh"])
        if x["deaths"]:
            assert m.deaths.iloc[0] == float(x["deaths"])


def test_c7_schema_parity(out):
    tmpl = jhu_template(latest_snapshot("cholera/jhu"))
    ours = pd.read_parquet(out["dir"] / "cholera_admin1.parquet")
    jng = tmpl[tmpl.location_name.str.startswith("AFR::NGA::") & (tmpl.spatial_scale == "admin1")]
    jng = jng[jng.TL >= jng.TL.max().replace(month=1, day=1)]
    ng = ours[ours.country == "NGA"][list(tmpl.columns)]
    assert list(ng.columns) == list(tmpl.columns)
    assert ng.dtypes.astype(str).tolist() == tmpl.dtypes.astype(str).tolist()
    both = pd.concat([jng, ng], ignore_index=True)
    assert both.dtypes.astype(str).tolist() == tmpl.dtypes.astype(str).tolist()
    assert len(both) == len(jng) + len(ng)
    b = out["report"]["jhu_boundary"]
    assert b["last_TL"] == "2022-08-15" and len(b["series"]) > 40


def test_c8_deterministic(out, tmp_path):
    build(out=tmp_path)
    a = {p.name: p.read_bytes() for p in out["dir"].iterdir()}
    b = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    assert a.keys() == b.keys() and all(a[k] == b[k] for k in a)


def test_audit_scopes_cholera_sources(tmp_path):
    """Cholera sources may only appear under cholera/; restricted 001 sources never there."""
    from crisis_data.publish import audit_bundle, cholera_sources, gate, load_cfg

    cfg = load_cfg()
    assert gate(cfg) == []
    assert cholera_sources(cfg) == {"ncdc", "sdn_arcgis", "who_adm0", "jhu", "codab"}
    (tmp_path / "cholera").mkdir()
    pd.DataFrame({"source": ["ncdc"], "sCh": [1]}).to_csv(tmp_path / "cholera" / "ok.csv", index=False)
    assert audit_bundle(tmp_path, cfg) == []
    pd.DataFrame({"source": ["fewsnet"], "sCh": [1]}).to_csv(tmp_path / "cholera" / "bad.csv", index=False)
    pd.DataFrame({"source": ["ncdc"], "sCh": [1]}).to_csv(tmp_path / "outside.csv", index=False)
    probs = audit_bundle(tmp_path, cfg)
    assert any("cholera/bad.csv" in p and "fewsnet" in p for p in probs)
    assert any(p.startswith("outside.csv") for p in probs)
    bad = {**cfg, "ncdc": {**cfg["ncdc"], "approved": None}}
    assert any("ncdc" in p for p in gate(bad))


@pytest.mark.parametrize("damage", ["no_dir", "missing_file", "empty_file"])
def test_publish_fails_closed_without_cholera(tmp_path, monkeypatch, capsys, damage):
    """A missing or incomplete cholera build refuses publish and deploys nothing (else /cholera/ vanishes)."""
    import crisis_data.publish as pub
    from crisis_data.paths import OUT

    out = tmp_path / "out"
    out.mkdir()
    for n in ("panel_long", "fx_rates", "coverage", "coverage_summary"):
        shutil.copyfile(OUT / f"{n}.parquet", out / f"{n}.parquet")
    shutil.copyfile(OUT / "build_report.json", out / "build_report.json")
    if damage != "no_dir":
        shutil.copytree(OUT / "cholera", out / "cholera")
        if damage == "missing_file":
            (out / "cholera" / "checks.parquet").unlink()
        else:
            (out / "cholera" / "cholera_admin1.csv").write_bytes(b"")
    deployed = []
    monkeypatch.setattr(pub, "deploy", lambda *a, **k: deployed.append(a) or "x")
    dest = tmp_path / "public"
    assert pub.main(dry_run=False, out=out, dest=dest) == 1
    assert deployed == [] and not dest.exists()
    assert "cholera build incomplete" in capsys.readouterr().err


C9_URL = os.environ.get("CRISIS_DATA_C9_URL")


@pytest.mark.skipif(not C9_URL, reason="set CRISIS_DATA_C9_URL to the live /cholera/ URL")
def test_c9_downloads_match_checksums():
    base = C9_URL.rstrip("/") + "/"
    sums = urllib.request.urlopen(base + "data/SHA256SUMS", timeout=60).read().decode()
    lines = [x.split("  ") for x in sums.strip().splitlines()]
    assert len(lines) >= 8
    for h, name in lines:
        body = urllib.request.urlopen(base + "data/" + name, timeout=120).read()
        assert hashlib.sha256(body).hexdigest() == h, name
    page = urllib.request.urlopen(base, timeout=60).read().decode()
    assert 'href="../"' in page
    root = urllib.request.urlopen(base.rsplit("cholera/", 1)[0], timeout=60).read().decode()
    assert 'href="cholera/"' in root


@pytest.mark.skipif(not (C9_URL and os.environ.get("NODE_PATH") and shutil.which("node")),
                    reason="needs CRISIS_DATA_C9_URL, node and NODE_PATH with playwright")
def test_c9_map_and_chart_render(tmp_path):
    shot = os.environ.get("CRISIS_DATA_C9_SHOT", str(tmp_path / "cholera.png"))
    r = subprocess.run(["node", str(ROOT / "tests" / "c9_render.cjs"), C9_URL, shot],
                       capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stdout + r.stderr
