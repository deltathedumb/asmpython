"""Run conformance cases against a Python implementation via a shim.

    python conformance/harness.py --shim cpython
    python conformance/harness.py --shim asmpython --tier spec,cpython
    python conformance/harness.py --shim asmpython --filter numeric/ --verbose

Exit codes: 0 = every counted case passed; 1 = at least one counted failure.
`impl`-tier cases are run and reported but never counted (see README.md).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"
SHIMS = ROOT / "shims"

#: Tiers whose failures count against an implementation. `impl` is recorded
#: but excluded: those are CPython accidents, not language guarantees, and
#: failing another implementation for them is how a suite loses its audience.
COUNTED_TIERS = ("spec", "cpython")
VALID_TIERS = ("spec", "cpython", "impl")


@dataclass
class Case:
    id: str
    path: Path
    tier: str
    ref: str
    expect: str
    skip_note: str = ""
    #: Lowest CPython that has the feature, as (major, minor). A case for
    #: syntax introduced in 3.12 is not a divergence on 3.11 -- it is a
    #: SyntaxError, and scoring it as a failure would say the language requires
    #: something the language did not yet have.
    min_python: tuple[int, ...] = ()


@dataclass
class Result:
    case: Case
    status: str          # PASS | FAIL | REFUSED | TIMEOUT | ERROR
    detail: str = ""

    @property
    def counted(self) -> bool:
        return self.case.tier in COUNTED_TIERS

    @property
    def ok(self) -> bool:
        return self.status == "PASS"


class CaseError(Exception):
    """A malformed case. Always fatal: a suite that silently skips its own
    broken cases reports a score it has not earned."""


def parse_case(path: Path) -> Case:
    """Read a case file into a Case, or raise CaseError.

    Header fields must precede `# expect:` -- every `#` line after that marker
    is expected stdout, so a trailing field would quietly become an extra
    expected line and the case would fail for a reason unrelated to the
    implementation.
    """
    text = path.read_text(encoding="utf-8")
    fields: dict[str, str] = {}
    expect_lines: list[str] = []
    collecting = False

    for raw in text.splitlines():
        s = raw.strip()
        if not collecting:
            if s.startswith("# expect:"):
                collecting = True
                continue
            if s.startswith("#") and ":" in s:
                key, _, val = s[1:].partition(":")
                key = key.strip()
                if key in ("tier", "ref", "skip", "min-python"):
                    fields[key] = val.strip()
            continue
        if s.startswith("#"):
            rest = s.lstrip("#")
            # Strip one conventional space only -- expected output may itself
            # be indented (right-justified formatting, nested repr).
            expect_lines.append(rest[1:] if rest.startswith(" ") else rest)
        else:
            break

    case_id = path.relative_to(CASES).with_suffix("").as_posix()

    if not collecting:
        raise CaseError(f"{case_id}: no `# expect:` block")
    tier = fields.get("tier", "")
    if tier not in VALID_TIERS:
        raise CaseError(
            f"{case_id}: tier must be one of {VALID_TIERS}, got {tier!r}"
        )
    ref = fields.get("ref", "")
    if tier == "spec" and not ref:
        raise CaseError(
            f"{case_id}: tier `spec` asserts the LANGUAGE requires this, so it "
            f"must cite a reference section via `# ref:`. If no citation "
            f"exists, the honest tier is `cpython` or `impl`."
        )
    min_python: tuple[int, ...] = ()
    if fields.get("min-python"):
        raw_mp = fields["min-python"]
        try:
            min_python = tuple(int(p) for p in raw_mp.split("."))
        except ValueError:
            raise CaseError(
                f"{case_id}: min-python must be a dotted version like 3.12, "
                f"got {raw_mp!r}"
            ) from None

    return Case(
        id=case_id,
        path=path,
        tier=tier,
        ref=ref,
        expect="\n".join(expect_lines).rstrip("\n"),
        skip_note=fields.get("skip", ""),
        min_python=min_python,
    )


def case_groups() -> list[str]:
    """Selectable group names, in sorted order.

    A group is any directory prefix of a case id, so both `pep` and the more
    specific `generated/boundary` are selectable and the caller picks the
    granularity they want.
    """
    groups: set[str] = set()
    for path in CASES.rglob("*.py"):
        parts = path.relative_to(CASES).with_suffix("").parts[:-1]
        for i in range(1, len(parts) + 1):
            groups.add("/".join(parts[:i]))
    return sorted(groups)


def discover(filter_sub: str = "", tiers: tuple[str, ...] = VALID_TIERS,
             groups: tuple[str, ...] = ()) -> list[Case]:
    cases: list[Case] = []
    problems: list[str] = []
    gated = 0
    for path in sorted(CASES.rglob("*.py")):
        try:
            case = parse_case(path)
        except CaseError as exc:
            problems.append(str(exc))
            continue
        if groups and not any(
            case.id == g or case.id.startswith(g + "/") for g in groups
        ):
            continue
        if filter_sub and filter_sub not in case.id:
            continue
        if case.tier not in tiers:
            continue
        if case.min_python and sys.version_info[:len(case.min_python)] < case.min_python:
            # Gated on the CPython running the SUITE, because that is what
            # derives and re-validates the expectation. A case using 3.12
            # syntax is not a divergence on 3.11 -- regen cannot even parse it
            # there, so there is no expectation to hold anyone to.
            gated += 1
            continue
        cases.append(case)
    if problems:
        raise CaseError("malformed cases:\n  " + "\n  ".join(problems))
    if gated:
        # Announced, never silent: a suite that quietly drops cases reports a
        # score it did not earn.
        v = ".".join(str(p) for p in sys.version_info[:2])
        print(f"note: {gated} case(s) gated by `min-python` above this "
              f"CPython ({v})")
    return cases


def load_shim(name: str):
    """Import shims/<name>.py. A shim exposes run(case_path, timeout) ->
    (stdout, stderr, returncode); returncode None means it refused to run the
    program at all (a compiler rejecting the source, say)."""
    path = SHIMS / f"{name}.py"
    if not path.exists():
        raise SystemExit(f"no shim {name!r} at {path}")
    spec = importlib.util.spec_from_file_location(f"_shim_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    if not hasattr(mod, "run"):
        raise SystemExit(f"shim {name!r} defines no run()")
    return mod


def _normalize(text: str) -> str:
    """Compare on content, not line-ending or trailing-whitespace accidents."""
    text = text.replace("\r\n", "\n").rstrip("\n")
    return "\n".join(line.rstrip() for line in text.split("\n"))


def run_case(case: Case, shim, timeout: int, paranoid: bool) -> Result:
    if case.skip_note:
        return Result(case, "SKIP", case.skip_note)
    try:
        out, err, code = shim.run(str(case.path), timeout)
    except TimeoutError:
        return Result(case, "TIMEOUT", f"exceeded {timeout}s")
    except Exception as exc:  # a shim bug must not look like a case failure
        return Result(case, "ERROR", f"shim raised: {exc!r}")

    if code is None:
        return Result(case, "REFUSED", (err or out or "").strip()[:400])
    if code != 0:
        return Result(case, "FAIL", f"exit {code}\n{(err or '').strip()[:400]}")

    got = _normalize(out)
    want = _normalize(case.expect)
    if got != want:
        return Result(case, "FAIL", f"--- want ---\n{want}\n--- got ---\n{got}")

    if paranoid:
        # A second run in a fresh process. Catches nondeterminism on the
        # IMPLEMENTATION side that regen.py's own double-run cannot see,
        # because regen only ever observes CPython.
        out2, _, code2 = shim.run(str(case.path), timeout)
        if code2 == 0 and _normalize(out2) != got:
            return Result(
                case, "FAIL",
                f"nondeterministic across runs:\n--- 1 ---\n{got}\n--- 2 ---\n{_normalize(out2)}",
            )
    return Result(case, "PASS")


#: Marks used in the matrix view. Deliberately single-character so a wide grid
#: still fits, and deliberately distinct for REFUSED: "cannot compile this" and
#: "compiles to the wrong answer" are both non-conformance but different bugs,
#: and collapsing them loses the distinction that tells you where to look.
_MARK = {"PASS": ".", "FAIL": "X", "REFUSED": "C", "TIMEOUT": "T",
         "ERROR": "E", "SKIP": "-"}


def print_matrix(results: list[Result]) -> None:
    """Collapse a generated cross-product into a grid.

    This is the whole reason generated cases are named after their axis
    coordinates. `generated/boundary/list-roundtrip/str` is the cell
    (list-roundtrip x str); if it fails while (list-roundtrip x int) passes,
    the defect IS that cell and no program needs reading.

    Without this, a generated suite just inflates a failure count that is
    already beyond anyone's capacity to triage. With it, hundreds of failures
    become a shape: a row that fails everywhere is a broken consumer, a column
    that fails everywhere is a broken value kind, and a scatter is neither.
    """
    families: dict[str, dict[str, dict[str, str]]] = {}
    for r in results:
        parts = r.case.id.split("/")
        if len(parts) != 4 or parts[0] != "generated":
            continue
        _, family, row, col = parts
        families.setdefault(family, {}).setdefault(row, {})[col] = r.status

    for family, rows in sorted(families.items()):
        cols = sorted({c for cells in rows.values() for c in cells})
        width = max((len(r) for r in rows), default=8) + 2
        # Each cell is ONE character and the headers run vertically, so the
        # column width has nothing to do with the name length. Deriving it from
        # the longest name made every column ~13 wide: a 19-column product came
        # out 250 characters across, wrapped in any terminal, and wrapping is
        # exactly what destroys a grid -- the whole point is that a row and a
        # column are visually straight.
        colw = 2

        print(f"\n=== {family} ===")
        # Column headers written vertically: names are long and a grid this
        # wide is unreadable with them inline.
        for depth in range(max(len(c) for c in cols)):
            line = " " * width
            for c in cols:
                line += (c[depth] if depth < len(c) else " ").ljust(colw)
            print(line)
        print("-" * (width + colw * len(cols)))

        row_fail: dict[str, int] = {}
        col_fail: dict[str, int] = {c: 0 for c in cols}
        for row in sorted(rows):
            line = row.ljust(width)
            fails = 0
            for c in cols:
                st = rows[row].get(c, "SKIP")
                line += _MARK.get(st, "?").ljust(colw)
                if st not in ("PASS", "SKIP"):
                    fails += 1
                    col_fail[c] += 1
            row_fail[row] = fails
            print(f"{line} {fails}" if fails else line)
        print("-" * (width + colw * len(cols)))
        print(f"  legend: {'  '.join(f'{v}={k.lower()}' for k, v in _MARK.items())}")

        # Per-column totals go in a LIST, not a footer row. A single digit per
        # column looked tidy and was actively misleading: 15-of-15 rendered as
        # "5" once it wrapped, which is the opposite of what a summary is for.
        n_rows = len(rows)
        n_cols = len(cols)
        saturated_cols = [c for c in cols if col_fail[c] == n_rows]
        saturated_rows = [r for r in rows if row_fail[r] == n_cols]

        if saturated_cols:
            # A column failing on EVERY trip is broken in the value kind
            # itself, not in any particular way of moving it. These are the
            # cheapest wins: one fix clears a whole column.
            print(f"  broken in the KIND (fail on all {n_rows} rows): "
                  + ", ".join(saturated_cols))
        if saturated_rows:
            print(f"  broken in the TRIP (fail on all {n_cols} cols): "
                  + ", ".join(saturated_rows))

        print("  per-column failures: " + ", ".join(
            f"{c}={col_fail[c]}" for c in sorted(cols, key=lambda c: -col_fail[c])
            if col_fail[c]))
        print("  per-row failures:    " + ", ".join(
            f"{r}={row_fail[r]}" for r in sorted(rows, key=lambda r: -row_fail[r])
            if row_fail[r]))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shim", default="cpython")
    ap.add_argument("--tier", default=",".join(VALID_TIERS),
                    help="comma-separated tiers to RUN (scoring still excludes impl)")
    ap.add_argument("--filter", default="", help="substring of the case id")
    ap.add_argument("--groups", default="",
                    help="comma-separated case groups, e.g. pep,functions or "
                         "generated/boundary. Use --list-groups to see them.")
    ap.add_argument("--list-groups", action="store_true",
                    help="print the selectable groups with case counts and exit")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("-j", "--jobs", type=int, default=8)
    ap.add_argument("--paranoid", action="store_true",
                    help="run every case twice and fail on disagreement")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--matrix", action="store_true",
                    help="collapse generated cross-products into a grid")
    ap.add_argument("--json", metavar="PATH",
                    help="write the full per-case result as JSON")
    args = ap.parse_args(argv)

    tiers = tuple(t.strip() for t in args.tier.split(",") if t.strip())
    for t in tiers:
        if t not in VALID_TIERS:
            raise SystemExit(f"unknown tier {t!r}; valid: {VALID_TIERS}")

    known = case_groups()
    if args.list_groups:
        all_cases = discover(tiers=VALID_TIERS)
        for g in known:
            n = sum(1 for c in all_cases
                    if c.id == g or c.id.startswith(g + "/"))
            print(f"  {n:5d}  {g}")
        return 0

    groups = tuple(g.strip() for g in args.groups.split(",") if g.strip())
    # A mistyped group must not quietly select nothing. Zero cases scores
    # 100%, which is the most dangerous possible way to be wrong.
    unknown = [g for g in groups if g not in known]
    if unknown:
        raise SystemExit(
            f"unknown group(s): {', '.join(unknown)}\n"
            f"valid groups:\n  " + "\n  ".join(known)
        )

    cases = discover(args.filter, tiers, groups)
    if not cases:
        print("no cases matched")
        return 0
    shim = load_shim(args.shim)

    print(f"pyconform: {len(cases)} case(s), shim={args.shim}, tiers={','.join(tiers)}")
    results: list[Result] = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futs = {pool.submit(run_case, c, shim, args.timeout, args.paranoid): c
                for c in cases}
        for fut in as_completed(futs):
            results.append(fut.result())

    results.sort(key=lambda r: r.case.id)
    counted = [r for r in results if r.counted]
    passed = [r for r in counted if r.ok]
    impl = [r for r in results if not r.counted]

    for r in results:
        if r.ok:
            if args.verbose:
                print(f"  [pass    ] {r.case.id}")
            continue
        mark = r.status.lower().ljust(8)
        scored = "" if r.counted else "  (impl tier, not counted)"
        print(f"  [{mark}] {r.case.id}{scored}")
        if r.detail and (args.verbose or r.counted):
            print("      " + r.detail.replace("\n", "\n      "))

    if args.matrix:
        print_matrix(results)

    if args.json:
        # Per-case rather than aggregate: a score alone cannot tell "fixed 3,
        # broke 3" from "changed nothing", and that is the comparison anyone
        # tracking an implementation over time actually wants. `detail` is
        # deliberately omitted -- it holds a full stdout diff, which would
        # dominate the file and churn on unrelated formatting changes.
        payload = {
            "shim": args.shim,
            "tiers": list(tiers),
            "counted": len(counted),
            "passed": len(passed),
            "cases": {
                r.case.id: {"status": r.status, "tier": r.case.tier,
                            "counted": r.counted}
                for r in results
            },
        }
        out = Path(args.json)
        if out.parent != Path(""):
            out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        print(f"wrote {out} ({len(results)} case(s))")

    pct = (100.0 * len(passed) / len(counted)) if counted else 100.0
    print(f"\nconformance: {len(passed)}/{len(counted)} ({pct:.1f}%) "
          f"on {'+'.join(t for t in tiers if t in COUNTED_TIERS) or 'nothing'}")
    if impl:
        impl_ok = sum(1 for r in impl if r.ok)
        print(f"impl-tier divergence: {len(impl) - impl_ok}/{len(impl)} differ "
              f"(recorded, not counted)")
    return 0 if len(passed) == len(counted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
