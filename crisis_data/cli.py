"""crisis-data CLI: fetch, build, publish."""

from __future__ import annotations

import argparse
import json
import sys

FETCHERS = ("wfp", "fewsnet", "jmmi", "codab")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="crisis-data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch", help="download raw snapshots to data/raw/<source>/<date>/")
    f.add_argument("--source", choices=FETCHERS, action="append", help="repeatable; default all")
    f.add_argument("--date", help="snapshot directory name (default today)")
    sub.add_parser("build", help="data/raw -> data/out (panel_long, panel_merged, coverage)")
    p = sub.add_parser("publish", help="license gate, build + audit the public bundle, deploy to gh-pages")
    p.add_argument("--dry-run", action="store_true", help="build and audit data/out/public only; send nothing")
    sub.add_parser("refresh", help="weekly job: fetch all, build, publish; writes freshness metrics")
    ch = sub.add_parser("cholera", help="spec 002 subnational cholera backfill")
    ch.add_argument("action", choices=("fetch", "build", "publish"))
    ch.add_argument("--source", action="append", help="fetch only: repeatable; default all")
    ch.add_argument("--date", help="fetch only: snapshot directory name (default today)")
    ch.add_argument("--dry-run", action="store_true", help="publish only: build and audit, send nothing")
    a = ap.parse_args(argv)

    if a.cmd == "fetch":
        import importlib

        failed = []
        for name in a.source or FETCHERS:
            mod = importlib.import_module(f"crisis_data.fetch.{name}")
            try:
                print(name, json.dumps(mod.fetch(a.date)))
            except Exception as e:  # report every source, fail at the end
                print(f"{name}: FAILED {e}", file=sys.stderr)
                failed.append(name)
        return 1 if failed else 0
    if a.cmd == "build":
        from crisis_data.build.panel import build

        r = build()
        print(json.dumps({k: r[k] for k in ("rows", "match_rate", "last_month_seen")}, indent=2))
        return 0
    if a.cmd == "publish":
        from crisis_data.publish import main as pub

        return pub(dry_run=a.dry_run)
    if a.cmd == "cholera":
        from crisis_data.cholera import cli as ccli

        return ccli.run(a)
    if a.cmd == "refresh":
        from crisis_data.refresh import run

        return run()
    return 2


if __name__ == "__main__":
    sys.exit(main())
