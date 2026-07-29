"""Run a NAMED SUBSET of the corpus through ``tests/runner.py``'s own machinery.

Two things the full runner cannot do:

  * **Replay a list.** Given the failure list from one run, re-run exactly
    those cases somewhere else. Comparing failure SETS is what tells you a
    change regressed nothing; comparing pass COUNTS does not, because a fix
    and a regression cancel out.

  * **Share the machine.** The build directory is a parameter, so a baseline
    can run in a ``git worktree`` at another commit *alongside* a full run
    without the two fighting over ``C:\\Temp``.

Typical use -- prove a change regressed nothing, without paying for a second
full run::

    # after your change: collect what fails now
    grep '\\[FAIL\\]' after.txt | sed 's/.*\\[FAIL\\] //' | sort > fails.txt

    # replay just those against the baseline commit
    git worktree add C:/Temp/base <baseline-commit>
    python tests/subset_runner.py C:/Temp/base fails.txt C:/Temp/base_build

Every case that fails after should also fail at the baseline. If the two runs
also pass the same NUMBER of cases, "fails-after is a subset of fails-before"
plus "the sets are the same size" means the pass sets are identical -- a
regression proof that costs one subset run instead of one full run.

Note this checks OBSERVABLE pass/fail. It cannot see a miscompile in a case
that fails in both states; ``tests/diff_passes.py --mode record/check`` is the
tool for that, and the two are complementary.

Usage:
    python tests/subset_runner.py <repo-root> <names-file> <build-dir>

`names-file` is whitespace-separated case file names (``157_re_module.py``),
resolved against ``tests/cases`` then ``tests/cases_fail``.
Exit status is 1 if any named case passed, 0 if none did -- so the common
"assert these all still fail" check can gate a script.
"""
import pathlib
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[-6], file=sys.stderr)
        print(
            "usage: subset_runner.py <repo-root> <names-file> <build-dir>",
            file=sys.stderr,
        )
        return 2

    root = pathlib.Path(argv[1]).resolve()
    names = pathlib.Path(argv[2]).read_text().split()
    build = pathlib.Path(argv[3])
    build.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(root))

    from tests import runner

    # The build dir is a module constant in runner.py; point it at ours so two
    # runs cannot overwrite each other's executables.
    runner.BUILD = build
    target = runner._detect_target()

    passed: list[str] = []
    failed: list[str] = []
    missing: list[str] = []
    for name in names:
        pos = root / "tests" / "cases" / name
        neg = root / "tests" / "cases_fail" / name
        try:
            if pos.exists():
                ok = runner.run_positive(pos, target).ok
            elif neg.exists():
                ok = runner.run_negative(neg, target).ok
            else:
                missing.append(name)
                continue
        except Exception as exc:
            # A crash in the harness (a build that produced no exe, a hung
            # binary) is a failure of the case, not of the run -- keep going,
            # or one bad case aborts the whole comparison.
            print(f"!! {name}: {type(exc).__name__}: {exc}")
            ok = False
        (passed if ok else failed).append(name)

    print(f"subset: {len(passed)} passed, {len(failed)} failed of {len(names)}")
    for n in missing:
        print(f"?? not found: {n}")
    for n in passed:
        print(f"PASSED {n}")
    return 1 if passed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
