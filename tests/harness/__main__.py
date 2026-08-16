"""`python -m tests.harness` -- the command that runs the suite."""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from . import cache, snapshot
from .collect import collect
from .report import Outcome
from .run import GUARDS, run

ROOT = Path(__file__).resolve().parents[2]

_MARK = {Outcome.PASS: ".", Outcome.FAIL: "F", Outcome.SKIP: "s",
         Outcome.BLOCKED: "b"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.harness",
        description="Run the asmpython test suite.")
    parser.add_argument("targets", nargs="*", default=None,
                        help="paths to collect from (default: tests/asmpython)")
    parser.add_argument("-k", dest="match", default="",
                        help="run only tests whose id contains this")
    parser.add_argument("-j", dest="jobs", type=int, default=0,
                        help="worker processes (default: cores-1, 1 = in "
                             "this process)")
    parser.add_argument("-x", dest="stop_after", nargs="?", type=int,
                        const=1, default=0,
                        help="stop after N failures (default 1)")
    parser.add_argument("--cached", action="store_true",
                        help="skip tests that passed and whose inputs have "
                             "not changed")
    parser.add_argument("--slowest", type=int, default=0,
                        help="list the N slowest tests afterwards")
    parser.add_argument("-q", dest="quiet", action="store_true",
                        help="one line per failure instead of the detail")
    parser.add_argument("--live-src", action="store_true",
                        help="import from src/ directly instead of a "
                             "snapshot; editing src/ mid-run then makes the "
                             "result meaningless")
    parser.add_argument("--keep-src", action="store_true",
                        help="leave the snapshot behind, to inspect what a "
                             "run actually measured")
    args = parser.parse_args(argv)

    # A FROZEN COPY OF THE COMPILER, taken before anything imports it. The run
    # measures this tree from start to finish, so `src/` is free to change
    # underneath -- see `snapshot`. Without it, editing during a run produces
    # a number describing a tree that never existed.
    frozen = None
    if not args.live_src:
        frozen = snapshot.take(ROOT, f"run-{os.getpid()}")
        snapshot.publish(frozen)

    try:
        return _run(args, frozen)
    finally:
        if frozen is not None and not args.keep_src:
            snapshot.discard(frozen)


def _run(args, frozen) -> int:
    targets = args.targets or ["tests/asmpython"]
    tests = collect(ROOT, targets)
    if args.match:
        tests = [t for t in tests if args.match in t.id]
    if not tests:
        print("no tests collected")
        return 1

    stamp = cache.fingerprint(ROOT)
    tests, cached = cache.apply(ROOT, tests, stamp, use_cache=args.cached)
    if not tests:
        print(f"nothing to run: {cached} tests already passed at this "
              f"revision")
        return 0

    missing = [name for name, probe in GUARDS.items() if not probe()]
    if missing:
        print(f"note: {', '.join(missing)} not found; tests needing them "
              f"will be blocked")

    started = time.perf_counter()
    shown = 0

    def progress(result):
        nonlocal shown
        sys.stdout.write(_MARK[result.outcome])
        shown += 1
        if shown % 72 == 0:
            sys.stdout.write(f" [{shown}/{len(tests)}]\n")
        sys.stdout.flush()

    report = run(tests, jobs=args.jobs, on_result=progress,
                 stop_after=args.stop_after)
    print()

    if not args.quiet:
        for result in report.results:
            if result.outcome is Outcome.FAIL:
                print(f"\nFAILED {result.id}\n  {result.message}")
                if result.detail:
                    print("\n".join("  " + line
                                    for line in result.detail.splitlines()))

    if args.slowest:
        print("\nslowest:")
        for result in report.slowest(args.slowest):
            print(f"  {result.seconds:7.2f}s  {result.id}")

    elapsed = time.perf_counter() - started
    trailer = f" ({cached} cached)" if cached else ""
    print(f"\n{report.summary()}{trailer} in {elapsed:.1f}s")
    cache.save(ROOT, report, stamp)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
