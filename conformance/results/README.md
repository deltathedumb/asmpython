# Recorded results

Each file here is one `harness.py --json` run: a **point-in-time snapshot**,
recorded at a specific commit of both the suite and the implementation.

A snapshot goes stale in two independent ways, and only one of them is visible
in the numbers:

- the **implementation** changes, which is the point;
- the **suite** grows, which moves the denominator and makes the percentage
  incomparable to the previous file.

So do not read two snapshots as a trend. Diff them:

```text
python conformance/compare.py results/asmpython.json after.json
```

`compare.py` reports per case, separating REGRESSED (was passing, now failing)
from NEW (never passed, because the case did not exist). Only the first is a
problem, and only the first sets a nonzero exit status. A raw score cannot tell
"fixed three, broke three" from "changed nothing" — both leave the number
unmoved, and one of them is a bug you shipped.

`asmpython.json` was recorded against a 585-case suite. The suite is larger now,
so its percentage is not comparable to a current run; its per-case entries still
are, which is what `compare.py` uses.
