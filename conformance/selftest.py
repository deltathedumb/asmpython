"""The suite's own correctness check: CPython must score 100%.

    python conformance/selftest.py

If CPython fails a `spec` or `cpython` case, the bug is in the SUITE, not in
any implementation. Exactly three things can cause it:

  1. A wrong expectation -- hand-typed rather than derived, or gone stale.
  2. A nondeterministic case -- a different correct answer each run, which
     regen.py's double-run should have refused.
  3. A mis-tiered accident -- an implementation detail asserted as though the
     language required it, which is the failure that would quietly make the
     suite unfair to every implementation that is not CPython.

Hand-authored suites have no equivalent of this check, which is why they
accumulate cases asserting things no implementation should have to satisfy.

Also validates every case's METADATA, including `impl`-tier ones the score
ignores: a malformed case is a defect in the suite whatever tier it claims.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import (  # noqa: E402
    CaseError, COUNTED_TIERS, VALID_TIERS, discover, load_shim, run_case,
)


def main() -> int:
    try:
        cases = discover(tiers=VALID_TIERS)
    except CaseError as exc:
        print("SUITE DEFECT -- malformed cases:\n")
        print(exc)
        return 1

    if not cases:
        print("no cases yet")
        return 0

    shim = load_shim("cpython")
    print(f"selftest: {len(cases)} case(s) against CPython {sys.version.split()[0]}")

    bad: list[tuple[str, str, str]] = []
    impl_diverging = 0
    for case in cases:
        # --paranoid: a second run catches a case whose answer moves between
        # processes even though regen recorded one of them successfully.
        res = run_case(case, shim, timeout=30, paranoid=True)
        if res.ok:
            continue
        if case.tier in COUNTED_TIERS:
            bad.append((case.id, case.tier, res.detail or res.status))
        else:
            impl_diverging += 1

    if impl_diverging:
        print(f"note: {impl_diverging} impl-tier case(s) did not match; that is "
              f"allowed and not counted")

    if not bad:
        counted = sum(1 for c in cases if c.tier in COUNTED_TIERS)
        print(f"OK: CPython passes all {counted} counted case(s)")
        return 0

    print(f"\nSUITE DEFECT -- CPython fails {len(bad)} counted case(s).")
    print("This is a bug in the suite. Check, in order: a hand-written "
          "expectation, a nondeterministic case, or a CPython accident "
          "mis-tiered as `spec`/`cpython`.\n")
    for cid, tier, detail in bad:
        print(f"  [{tier}] {cid}")
        print("      " + (detail or "").replace("\n", "\n      "))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
