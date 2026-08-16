"""Differential test: did a change alter observable program behavior?

The ordinary test suite cannot see a silent miscompile. It scores *identically*
under a change that corrupts output, because a case that failed before and fails
after is a pass/fail no-op no matter how wrong the bytes got. This harness
compares the actual runtime behavior of the same source compiled two ways:

    compile each tests/cases/*.py twice -> run both -> diff stdout + exit code

Any difference is a miscompile. Cases that already fail to compile or run in the
baseline are skipped: those are pre-existing corpus gaps, not regressions.

Two modes, for the two kinds of change:

``passes`` (default) -- for a change that is *selected* at build time.
    Builds each case with no passes and again with ``--passes SPEC``, in one
    run. This is the certification gate for a new optimization pass.

        python tests/diff_passes.py --passes o2
        python tests/diff_passes.py --passes mem2reg,constfold --sample 200

``record`` / ``check`` -- for a change that is *always on* (frontend, sema,
    lowering, codegen, regalloc). The two builds cannot coexist in one process,
    so record the baseline, apply the change, then check against it:

        git stash                                   # or start from clean beta
        python tests/diff_passes.py --mode record --state before.json
        git stash pop                               # apply your change
        python tests/diff_passes.py --mode check  --state before.json

    Native-vs-native across your own diff is the point: it separates a
    regression you introduced from a parity gap that was already there.

Exit status is 1 if anything diverged, so this can gate a commit.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CASES = REPO / "tests" / "cases"


#: A case whose own output varies between two runs of the SAME binary. Its
#: output embeds something that is not a function of the program -- in this
#: corpus, a heap address printed where a value was meant (a container repr'd
#: as its pointer). Comparing such a case across two builds is meaningless: it
#: differs every time, so it would be reported as a miscompile forever and
#: train the reader to ignore real ones.
NONDETERMINISTIC = ["NONDETERMINISTIC", ""]


def build_and_run(src: Path, exe: Path, passes: str | None, timeout: int,
                  runs: int = 2):
    """Compile ``src`` to ``exe`` and run it. None if it did not compile.

    Runs the binary ``runs`` times and returns :data:`NONDETERMINISTIC` if the
    results disagree, so a case that cannot be compared is excluded rather than
    counted as a difference.
    """
    cmd = [
        sys.executable, "-m", "asmpython", "build", str(src),
        "-o", str(exe), "--no-pyinbin-fallback",
    ]
    if passes:
        cmd += ["--passes", passes]
    try:
        built = subprocess.run(
            cmd, capture_output=True, text=True, cwd=REPO, timeout=timeout * 3
        )
    except subprocess.TimeoutExpired:
        return None
    if built.returncode != 0:
        return None

    first = None
    for _ in range(max(1, runs)):
        try:
            ran = subprocess.run(
                [str(exe)], capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            return ["TIMEOUT", ""]
        result = [ran.returncode, ran.stdout]
        if first is None:
            first = result
        elif result != first:
            return list(NONDETERMINISTIC)
    return first


def select_cases(sample: int, pattern: str) -> list[Path]:
    found = sorted(CASES.glob(pattern))
    return found[:sample] if sample > 0 else found


def report(same: int, differ: int, skipped: int, failures: list[str],
           nondet: int = 0) -> int:
    print(f"\nidentical={same}  DIFFERENT={differ}  "
          f"skipped(pre-existing fail)={skipped}  nondeterministic={nondet}")
    for line in failures:
        print("  !!", line)
    if nondet:
        print(f"{nondet} case(s) excluded: their own output varies between two "
              f"runs of the same binary (a heap address printed as a value), so "
              f"they cannot be compared across builds.")
    if differ:
        print(f"\n{differ} divergence(s) -- this is a miscompile, not a corpus gap.")
    return 1 if differ else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--mode", choices=("passes", "record", "check"), default="passes")
    ap.add_argument("--passes", default=None,
                    help="pass spec for the variant build. Default 'o2' in "
                         "passes mode; in record/check the default is no passes "
                         "at all, since there the change under test is the tree "
                         "itself -- pass a spec only to test a change *under* "
                         "an optimization pipeline. 'none' forces no passes.")
    ap.add_argument("--state", type=Path,
                    help="baseline JSON file for record/check mode")
    ap.add_argument("--workdir", type=Path, default=None,
                    help="where to put the built executables (default: a temp dir)")
    ap.add_argument("--sample", type=int, default=0,
                    help="only the first N cases (0 = all)")
    ap.add_argument("--filter", default="*.py",
                    help="glob within tests/cases (e.g. '1[45]*.py')")
    ap.add_argument("--timeout", type=int, default=60, help="per-run seconds")
    ap.add_argument("--runs", type=int, default=2,
                    help="run each binary N times; a case whose own runs "
                         "disagree is nondeterministic and excluded (default 2)")
    args = ap.parse_args()

    if args.mode in ("record", "check") and args.state is None:
        ap.error("--state FILE is required for --mode record/check")

    if args.passes is None:
        args.passes = "o2" if args.mode == "passes" else "none"
    spec = None if args.passes.lower() == "none" else args.passes

    if args.workdir is None:
        import tempfile
        workdir = Path(tempfile.mkdtemp(prefix="diffpasses_"))
    else:
        workdir = args.workdir
        workdir.mkdir(parents=True, exist_ok=True)

    cases = select_cases(args.sample, args.filter)
    if not cases:
        print(f"no cases matched {args.filter!r} under {CASES}")
        return 1
    print(f"{len(cases)} case(s), mode={args.mode}, build dir {workdir}")

    # ---- record: snapshot current behavior and stop -----------------------
    if args.mode == "record":
        state = {}
        for case in cases:
            result = build_and_run(case, workdir / f"{case.stem}.exe", spec,
                                   args.timeout, args.runs)
            state[case.name] = result
        args.state.write_text(json.dumps(state, indent=1), encoding="utf-8")
        usable = sum(1 for v in state.values()
                     if v is not None and v != NONDETERMINISTIC)
        nd = sum(1 for v in state.values() if v == NONDETERMINISTIC)
        print(f"recorded {usable} usable baseline(s) of {len(state)} "
              f"({nd} nondeterministic, excluded) -> {args.state}")
        return 0

    # ---- check: compare current behavior against the recording ------------
    if args.mode == "check":
        state = json.loads(args.state.read_text(encoding="utf-8"))
        same = differ = skipped = nondet = 0
        failures: list[str] = []
        for case in cases:
            before = state.get(case.name)
            if before is None:
                skipped += 1          # did not build in the baseline either
                continue
            if before == NONDETERMINISTIC:
                nondet += 1
                continue
            after = build_and_run(case, workdir / f"{case.stem}.exe", spec,
                                  args.timeout, args.runs)
            if after is None:
                differ += 1
                failures.append(f"{case.name}: FAILED TO BUILD now (baseline built OK)")
            elif after == NONDETERMINISTIC:
                nondet += 1
            elif after == before:
                same += 1
            else:
                differ += 1
                failures.append(f"{case.name}: before={before!r} after={after!r}")
        return report(same, differ, skipped, failures, nondet)

    # ---- passes: build both variants in one run ---------------------------
    same = differ = skipped = nondet = 0
    failures: list[str] = []
    for case in cases:
        base = build_and_run(case, workdir / f"{case.stem}_base.exe", None,
                             args.timeout, args.runs)
        if base is None:
            skipped += 1
            continue
        if base == NONDETERMINISTIC:
            nondet += 1
            continue
        opt = build_and_run(case, workdir / f"{case.stem}_opt.exe", spec,
                            args.timeout, args.runs)
        if opt is None:
            differ += 1
            failures.append(
                f"{case.name}: FAILED TO COMPILE with {args.passes} (baseline OK)")
        elif opt == NONDETERMINISTIC:
            nondet += 1
        elif opt == base:
            same += 1
        else:
            differ += 1
            failures.append(f"{case.name}: base={base!r} opt={opt!r}")
    return report(same, differ, skipped, failures, nondet)


if __name__ == "__main__":
    raise SystemExit(main())
