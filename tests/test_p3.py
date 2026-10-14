"""Spec 001 P3 tests: T12 (refresh is safe to repeat and fails whole) and the freshness signal T10 reads.

T10 itself (a stale source fires the alert rule once) runs the provisioned rule in
~/Software/claude-telemetry/crisis/test_crisis_rules.py.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from crisis_data import publish as pub
from crisis_data import refresh as rf
from crisis_data.fetch.common import write_manifest


def _snap(raw: Path, source: str, date: str, files: dict[str, str]) -> Path:
    d = raw / source / date
    for name, body in files.items():
        (d / name).parent.mkdir(parents=True, exist_ok=True)
        (d / name).write_text(body)
    write_manifest(d, {"source": source, "fetched_at": date})
    return d


WFP_A = {"wfp_food_prices_sdn.csv": "date,price\n2026-08-15,1\n2026-09-15,2\n",
         "hdx_package_sdn.json": '{"views": 1}'}


def test_t12_settle_drops_identical_snapshot(tmp_path):
    _snap(tmp_path, "wfp", "2026-10-07", WFP_A)
    new = _snap(tmp_path, "wfp", "2026-10-14", {**WFP_A, "hdx_package_sdn.json": '{"views": 9}'})
    assert rf.settle(new) == "unchanged"
    assert not new.exists()
    assert rf.latest("wfp", tmp_path).name == "2026-10-07"


def test_t12_settle_keeps_changed_and_links_unchanged(tmp_path):
    old = _snap(tmp_path, "fewsnet", "2026-10-07", {"m/2026-09.csv": "id\n1\n", "m/2026-10.csv": "id\n"})
    new = _snap(tmp_path, "fewsnet", "2026-10-14", {"m/2026-09.csv": "id\n1\n", "m/2026-10.csv": "id\n2\n"})
    assert rf.settle(new) == "changed:1"
    assert (new / "m/2026-09.csv").stat().st_ino == (old / "m/2026-09.csv").stat().st_ino
    assert (new / "m/2026-10.csv").stat().st_ino != (old / "m/2026-10.csv").stat().st_ino
    assert rf.measure("fewsnet", new) == 2


def _fake_fetch(raw: Path, bodies: dict[str, dict[str, str]], fail: set[str] = frozenset()):
    def get(name):
        def fetch(date):
            if name in fail:
                raise RuntimeError("simulated outage")
            _snap(raw, name, date, bodies[name])
            return {}
        return fetch
    return get


BODIES = {"wfp": WFP_A, "fewsnet": {"m/2026-09.csv": "id\n1\n"},
          "jmmi": {"sdn/a.xlsx": "x"}, "codab": {"sdn.zip": "z"}}


def _run(tmp_path, date, bodies, now, fail=frozenset(), publish_rc=0):
    calls = []
    rc = rf.run(today=date, raw=tmp_path / "raw", state_path=tmp_path / "state.json",
                metrics_path=tmp_path / "m" / "crisis_data.prom", fetch=_fake_fetch(tmp_path / "raw", bodies, fail),
                build=lambda: calls.append("build"), publish=lambda: calls.append("publish") or publish_rc,
                clock=lambda: now)
    return rc, calls, rf.load_state(tmp_path / "state.json")


def test_t12_freshness_advances_only_on_new_rows(tmp_path):
    rc, calls, st = _run(tmp_path, "2026-10-07", BODIES, 1000)
    assert rc == 0 and calls == ["build", "publish"]
    assert {k: v["last_new_rows"] for k, v in st["sources"].items() if k != "codab"} == \
        {"wfp": 1000, "fewsnet": 1000, "jmmi": 1000}
    # Same data a week later: fetch ok, no new rows, previous snapshot stays latest.
    rc, _, st = _run(tmp_path, "2026-10-14", BODIES, 2000)
    assert rc == 0 and st["run_last_ok"] == 2000
    assert st["sources"]["wfp"] == {"fetch_last_ok": 2000, "last_new_rows": 1000, "rows": 2,
                                    "snapshot": "2026-10-07"}
    # WFP adds a row: only WFP advances.
    more = {**BODIES, "wfp": {**WFP_A, "wfp_food_prices_sdn.csv": WFP_A["wfp_food_prices_sdn.csv"] + "2026-10-15,3\n"}}
    _, _, st = _run(tmp_path, "2026-10-21", more, 3000)
    assert st["sources"]["wfp"]["last_new_rows"] == 3000 and st["sources"]["wfp"]["rows"] == 3
    assert st["sources"]["fewsnet"]["last_new_rows"] == 1000
    prom = (tmp_path / "m" / "crisis_data.prom").read_text()
    assert 'crisis_data_source_last_new_rows_timestamp_seconds{source="wfp"} 3000' in prom
    assert 'crisis_data_source_last_new_rows_timestamp_seconds{source="jmmi"} 1000' in prom
    assert "crisis_data_run_last_ok_timestamp_seconds 3000" in prom


def test_t12_failed_fetch_fails_run_and_publishes_nothing(tmp_path):
    _run(tmp_path, "2026-10-07", BODIES, 1000)
    rc, calls, st = _run(tmp_path, "2026-10-14", BODIES, 2000, fail={"jmmi"})
    assert rc == 1 and calls == []
    assert st["run_last_ok"] == 1000 and st["run_last_attempt"] == 2000
    assert st["sources"]["jmmi"]["fetch_last_ok"] == 1000
    assert st["sources"]["wfp"]["fetch_last_ok"] == 2000
    assert "crisis_data_run_last_ok_timestamp_seconds 1000" in (tmp_path / "m" / "crisis_data.prom").read_text()


def test_t12_failed_publish_fails_run(tmp_path):
    rc, calls, st = _run(tmp_path, "2026-10-07", BODIES, 1000, publish_rc=1)
    assert rc == 1 and calls == ["build", "publish"] and st["run_last_ok"] is None
    assert "crisis_data_run_last_ok" not in (tmp_path / "m" / "crisis_data.prom").read_text()


def test_t12_deploy_same_bundle_makes_no_commit(tmp_path):
    remote = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--quiet", "--bare", str(remote)], check=True)
    bundle = tmp_path / "bundle"
    (bundle / "data").mkdir(parents=True)
    (bundle / "data" / "SHA256SUMS").write_text("abc  x\n")
    (bundle / "index.html").write_text("<p>v1</p>\n")
    first = pub.deploy(bundle, remote=str(remote))
    assert first != "unchanged"
    assert pub.deploy(bundle, remote=str(remote)) == "unchanged"
    log = subprocess.run(["git", "--git-dir", str(remote), "rev-list", "--count", "gh-pages"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert log == "1"


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


def _fdw(monkeypatch, rows):
    from crisis_data.fetch import fewsnet

    monkeypatch.setattr(fewsnet, "PAUSE_S", 0)
    monkeypatch.setattr(fewsnet, "get", lambda url, params=None: _Resp(
        {"count": len(rows), "results": rows[params["offset"]:params["offset"] + params["page_size"]]}))
    return fewsnet


def test_t12_fewsnet_export_lag_accepted_when_explained(monkeypatch):
    import datetime as dt

    rows = [{"id": i, "created": "2026-06-01T00:00:00"} for i in range(259)] + \
           [{"id": 1000 + i, "created": "2026-10-13T00:00:00"} for i in range(12)]
    f = _fdw(monkeypatch, rows)
    csv_ids = {str(i) for i in range(259)}
    assert f._lag_rows("marketpricefacts", {}, 271, csv_ids, dt.date(2026, 10, 14)) == 12


def test_t12_fewsnet_old_missing_rows_still_fail(monkeypatch):
    import datetime as dt

    import pytest

    rows = [{"id": i, "created": "2026-06-01T00:00:00"} for i in range(271)]
    f = _fdw(monkeypatch, rows)
    with pytest.raises(RuntimeError, match="not explained by export lag"):
        f._lag_rows("marketpricefacts", {}, 271, {str(i) for i in range(259)}, dt.date(2026, 10, 14))
