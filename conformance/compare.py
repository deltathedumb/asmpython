"""Diff two `harness.py --json` results.

    python conformance/harness.py --shim asmpython --json before.json
    ... change the implementation ...
    python conformance/harness.py --shim asmpython --json after.json
    python conformance/compare.py before.json after.json

Exists because a score cannot tell "fixed three, broke three" from "changed
nothing". Both read 245/484. Only one of them is a problem, and it is the one
that looks like success.

That is not hypothetical: the same instrument for this project's own test corpus
(tests/baseline.py) was built after a deliberately injected regression and a
deliberately injected fix left the count at 766/1085 -> 766/1085, unchanged.

Exit status is 1 if anything REGRESSED, 0 otherwise -- so this is usable as a
CI gate that permits progress and forbids silent loss. A new failing case is not
a regression (it never passed); it is reported separately, because adding a case
that fails is how coverage is supposed to grow.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load(p: str) -> dict:
    d = json.loads(Path(p).read_text(encoding="utf-8"))
    if "cases" not in d:
        raise SystemExit(f"{p}: not a harness --json result")
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--counted-only", action="store_true",
                    help="ignore impl-tier cases entirely")
    args = ap.parse_args(argv)

    before, after = _load(args.before), _load(args.after)
    if before["shim"] != after["shim"]:
        print(f"note: comparing different shims "
              f"({before['shim']} -> {after['shim']})")

    b_cases, a_cases = before["cases"], after["cases"]

    def _ok(rec) -> bool:
        return rec["status"] == "PASS"

    def _keep(rec) -> bool:
        return rec["counted"] or not args.counted_only

    regressed, fixed, new_fail, new_pass, removed = [], [], [], [], []
    for cid in sorted(set(b_cases) | set(a_cases)):
        b, a = b_cases.get(cid), a_cases.get(cid)
        if a is None:
            if _keep(b):
                removed.append(cid)
            continue
        if not _keep(a):
            continue
        if b is None:
            (new_pass if _ok(a) else new_fail).append(cid)
        elif _ok(b) and not _ok(a):
            regressed.append(f"{cid}  ({a['status']})")
        elif not _ok(b) and _ok(a):
            fixed.append(cid)

    print(f"{before['passed']}/{before['counted']} -> "
          f"{after['passed']}/{after['counted']}")

    for title, items in (
        ("REGRESSED -- was passing, now failing", regressed),
        ("FIXED -- was failing, now passing", fixed),
        ("NEW, failing -- did not exist before", new_fail),
        ("NEW, passing -- did not exist before", new_pass),
        ("REMOVED -- existed before, gone now", removed),
    ):
        if items:
            print(f"\n{title} ({len(items)}):")
            for it in items:
                print(f"  {it}")

    if not any((regressed, fixed, new_fail, new_pass, removed)):
        print("\nidentical: every case has the same status")
    return 1 if regressed else 0


if __name__ == "__main__":
    raise SystemExit(main())
