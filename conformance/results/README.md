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

`asmpython.json` is the current snapshot of the compiler in `src/`.

Its predecessor measured a DIFFERENT COMPILER, and that is worth knowing before
anyone reads an old number as a regression. The shim invoked
`python -m asmpython` without putting this checkout on the path, so it resolved
to whichever `asmpython` was installed in site-packages — a released build of
the pre-rewrite compiler, not the tree it sits in, and on a 585-case suite.
`rewrite_zero_baseline.json` is the first run against the actual 3.14 compiler,
and it is 0/1668: the frontend accepted only function definitions at module
level, and every conformance case is a script.

TAKE A SNAPSHOT ONLY WHEN THE TREE BUILDS. A run against a working tree that
briefly does not compile records a score with no meaning — one such run came
back 409/1668 against a tree measuring 888 either side of it, because the
generated C failed to compile for part of it. `python -m asmpython build` on a
three-line program is a one-second check and it is worth doing first.
