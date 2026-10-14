"""`crisis-data cholera fetch|build|publish`."""

from __future__ import annotations

import json
import sys


def run(a) -> int:
    if a.action == "fetch":
        from crisis_data.cholera.fetch import FETCHERS

        failed = []
        for name in a.source or FETCHERS:
            try:
                print(name, json.dumps(FETCHERS[name](a.date)))
            except Exception as e:  # report every source, fail at the end
                print(f"{name}: FAILED {e}", file=sys.stderr)
                failed.append(name)
        return 1 if failed else 0
    if a.action == "build":
        from crisis_data.cholera.build import build

        r = build()
        print(json.dumps(r["summary"], indent=2))
        return 0
    if a.action == "publish":
        from crisis_data.publish import main as pub

        return pub(dry_run=a.dry_run)
    return 2
