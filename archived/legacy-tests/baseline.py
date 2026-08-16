"""Pin the corpus result set so a regression is distinguishable from a
pre-existing failure.

At 766/1085 the suite cannot answer the only question that matters during a
refactor: "did I break something?" A run that reports 764 passing is either a
2-case regression or a 2-case reshuffle of the 319 already-failing cases, and
`python -m tests.runner` alone cannot tell you which. That ambiguity is what
makes large-scale change unsafe here -- not the failure count itself.

This module records the exact per-case verdict into `tests/baseline.json` and
diffs later runs against it, classifying every difference as one of:

    REGRESSION  was OK, now FAIL   -- the only class that fails --check
    FIXED       was FAIL, now OK   -- progress; re-record to lock it in
    NEW         absent from the manifest (a case was added)
    REMOVED     in the manifest but no longer present

Only REGRESSION sets a nonzero exit code. FIXED is reported loudly but never
fails, so tightening the manifest stays a deliberate act rather than a
side effect of an unrelated run.

Usage:
    python -m tests.baseline --record            # run the suite, write manifest
    python -m tests.baseline --check             # run the suite, diff manifest
    python -m tests.baseline --record --from-file run.txt   # reuse a run's output
    python -m tests.baseline --check  --from-file run.txt
    python -m tests.baseline --show              # summarize the manifest

Extra runner flags pass straight through after `--`:
    python -m tests.baseline --check -- --backend x86-64 -j 8

TREE FINGERPRINT
The manifest records the git commit plus a hash of the uncommitted diff. A
manifest recorded on a dirty tree is still valid -- most refactor work happens
on dirty trees -- but `--check` warns when the fingerprint has moved, because a
clean diff against a manifest from a different tree is not evidence of anything.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "tests" / "baseline.json"

# Runner result lines look like:  "  [OK  ] 01_hello.py"  /  "  [FAIL] x.py"
_RESULT_RE = re.compile(r"^\s*\[(OK\s*|FAIL)\]\s+(\S+)")
_SUMMARY_RE = re.compile(r"^(\d+)/(\d+) passed")


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=30
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _tree_fingerprint() -> dict:
    """Identify the tree a manifest describes.

    The commit alone is not enough: this project is routinely measured on a
    working tree with uncommitted compiler changes, and two different dirty
    trees on the same commit produce different corpus results. Hashing the
    diff distinguishes them without storing the diff itself.
    """
    commit = _git("rev-parse", "HEAD")
    diff = _git("diff", "HEAD")
    return {
        "commit": commit or "(unknown)",
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "(unknown)",
        "dirty": bool(diff),
        "diff_sha256": hashlib.sha256(diff.encode("utf-8", "replace")).hexdigest()[:16]
        if diff
        else "",
    }


def _run_suite(runner_args: list[str]) -> str:
    """Run the corpus and return the runner's raw stdout."""
    cmd = [sys.executable, "-m", "tests.runner", *runner_args]
    print(f"running: {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    # The runner exits 1 when any case fails, which is the normal state here;
    # only a crash (no parsable summary) is treated as an error by the caller.
    return proc.stdout


def _parse_results(text: str) -> dict[str, bool]:
    """Map case filename -> passed. Raises if the output has no summary line,
    which means the runner died rather than merely reporting failures."""
    results: dict[str, bool] = {}
    saw_summary = False
    for line in text.splitlines():
        m = _RESULT_RE.match(line)
        if m:
            results[m.group(2)] = m.group(1).strip() == "OK"
            continue
        if _SUMMARY_RE.match(line.strip()):
            saw_summary = True
    if not saw_summary:
        raise SystemExit(
            "runner produced no '<n>/<n> passed' summary -- it crashed rather "
            "than reporting failures. Not writing or checking a manifest."
        )
    return results


def _load_manifest() -> dict:
    if not MANIFEST.exists():
        raise SystemExit(
            f"no manifest at {MANIFEST}. Run: python -m tests.baseline --record"
        )
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def _write_manifest(results: dict[str, bool], runner_args: list[str]) -> None:
    passing = sorted(n for n, ok in results.items() if ok)
    failing = sorted(n for n, ok in results.items() if not ok)
    payload = {
        "_comment": (
            "Pinned corpus baseline. 'failing' is the set of cases known to fail "
            "at this tree state; a case leaving that set is progress, a case "
            "entering it is a regression. Regenerate with "
            "`python -m tests.baseline --record`."
        ),
        "tree": _tree_fingerprint(),
        "runner_args": runner_args,
        "counts": {
            "total": len(results),
            "passing": len(passing),
            "failing": len(failing),
        },
        "passing": passing,
        "failing": failing,
    }
    MANIFEST.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(
        f"recorded {len(passing)}/{len(results)} passing "
        f"({len(failing)} known failures) -> {MANIFEST.relative_to(ROOT)}"
    )


def _check(results: dict[str, bool]) -> int:
    manifest = _load_manifest()
    was_passing = set(manifest["passing"])
    was_failing = set(manifest["failing"])
    known = was_passing | was_failing

    now_passing = {n for n, ok in results.items() if ok}
    now_failing = {n for n, ok in results.items() if not ok}
    seen = now_passing | now_failing

    regressions = sorted(was_passing & now_failing)
    fixed = sorted(was_failing & now_passing)
    added = sorted(seen - known)
    removed = sorted(known - seen)

    old = _tree_fingerprint()
    rec = manifest.get("tree", {})
    if (old.get("commit"), old.get("diff_sha256")) != (
        rec.get("commit"),
        rec.get("diff_sha256"),
    ):
        print(
            f"note: manifest was recorded at {rec.get('commit','?')[:8]}"
            f"{'+dirty' if rec.get('dirty') else ''}, "
            f"tree is now {old.get('commit','?')[:8]}"
            f"{'+dirty' if old.get('dirty') else ''}"
        )

    print(
        f"\nbaseline {manifest['counts']['passing']}/{manifest['counts']['total']} "
        f"-> current {len(now_passing)}/{len(results)}"
    )

    if regressions:
        print(f"\nREGRESSIONS ({len(regressions)}) -- was passing, now failing:")
        for n in regressions:
            print(f"  {n}")
    if fixed:
        print(f"\nFIXED ({len(fixed)}) -- was failing, now passing:")
        for n in fixed:
            print(f"  {n}")
    if added:
        print(f"\nNEW ({len(added)}) -- not in the manifest:")
        for n in added:
            status = "pass" if n in now_passing else "FAIL"
            print(f"  [{status}] {n}")
    if removed:
        print(f"\nREMOVED ({len(removed)}) -- in the manifest but not run:")
        for n in removed:
            print(f"  {n}")

    if not (regressions or fixed or added or removed):
        print("\nclean: identical to the pinned baseline")
    elif not regressions:
        print("\nno regressions")

    if fixed and not regressions:
        print("re-record to lock in the fixes: python -m tests.baseline --record")

    return 1 if regressions else 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    runner_args: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        runner_args = argv[i + 1:]
        argv = argv[:i]

    ap = argparse.ArgumentParser(prog="tests.baseline", description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--record", action="store_true", help="write the manifest")
    g.add_argument("--check", action="store_true", help="diff against the manifest")
    g.add_argument("--show", action="store_true", help="summarize the manifest")
    ap.add_argument(
        "--from-file",
        metavar="PATH",
        help="parse an existing runner output file instead of running the suite",
    )
    args = ap.parse_args(argv)

    if args.show:
        m = _load_manifest()
        t = m.get("tree", {})
        c = m["counts"]
        print(f"manifest: {MANIFEST.relative_to(ROOT)}")
        print(f"  tree:    {t.get('commit','?')[:12]} ({t.get('branch','?')})"
              f"{' +dirty ' + t.get('diff_sha256','') if t.get('dirty') else ''}")
        print(f"  runner:  {' '.join(m.get('runner_args') or []) or '(defaults)'}")
        print(f"  counts:  {c['passing']}/{c['total']} passing, "
              f"{c['failing']} known failures")
        return 0

    if args.from_file:
        text = Path(args.from_file).read_text(encoding="utf-8", errors="replace")
    else:
        text = _run_suite(runner_args)

    results = _parse_results(text)
    if not results:
        raise SystemExit("no case results parsed -- nothing to record or check")

    if args.record:
        _write_manifest(results, runner_args)
        return 0
    return _check(results)


if __name__ == "__main__":
    raise SystemExit(main())
