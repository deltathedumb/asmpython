"""What the last run knew, so this one need not rediscover it.

TWO THINGS ARE REMEMBERED, and they buy different speedups:

  * HOW LONG EACH TEST TOOK. Used only to order the next run slowest-first,
    which is what keeps every worker busy to the end. Wrong timings cost
    nothing but a worse packing.

  * WHAT PASSED, AND AGAINST WHAT. A test that passed and whose inputs have
    not changed since does not need to run again. The input is the compiler --
    everything under `src/` -- plus the test file itself, hashed together.
    That is coarse: touching one file in `src/` invalidates the whole suite.
    It is also the only version that cannot be WRONG, and a cache that
    sometimes skips a test that would now fail is worse than no cache.

`--all` ignores the cache. Use it before anything that matters; `--cached` is
for the loop where a file is edited every two minutes.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .report import Outcome, Report

CACHE = ".asmpython-tests.json"


def fingerprint(root: Path) -> str:
    """One hash over everything a test's outcome could depend on.

    Content, not mtimes: a checkout or a branch switch rewrites mtimes without
    changing anything, and a cache that invalidates on those is a cache nobody
    benefits from.
    """
    digest = hashlib.sha256()
    for base in ("src", "tests"):
        here = root / base
        if not here.is_dir():
            continue
        for path in sorted(here.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
    return digest.hexdigest()


def load(root: Path) -> dict:
    try:
        return json.loads((root / CACHE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save(root: Path, report: Report, stamp: str) -> None:
    """Record the run. Timings are kept for every outcome; the PASSED set is
    only meaningful together with the fingerprint it was measured at."""
    previous = load(root)
    weights = dict(previous.get("weights", {}))
    for result in report.results:
        if result.seconds:
            weights[result.id] = round(result.seconds, 4)
    passed = sorted(r.id for r in report.results if r.outcome is Outcome.PASS)
    # A partial run must not shrink the record: tests that were not run this
    # time keep whatever the last full run said about them.
    if previous.get("fingerprint") == stamp:
        passed = sorted(set(passed) | set(previous.get("passed", [])))
    (root / CACHE).write_text(
        json.dumps({"fingerprint": stamp, "passed": passed,
                    "weights": weights}, indent=1),
        encoding="utf-8")


def apply(root: Path, tests: list, stamp: str, *, use_cache: bool):
    """Attach known weights, and drop what already passed unchanged.

    Returns `(to_run, skipped_count)`. The count is reported rather than
    hidden: a run that says "1150 passed" having executed twelve of them is
    lying, and the summary says how many were taken on trust.
    """
    known = load(root)
    weights = known.get("weights", {})
    for test in tests:
        test.weight = weights.get(test.id, 0.0)
    if not use_cache or known.get("fingerprint") != stamp:
        return tests, 0
    already = set(known.get("passed", ()))
    keep = [t for t in tests if t.id not in already]
    return keep, len(tests) - len(keep)
