"""Score an implementation against the CPython oracle, via a shim.

    python conformance/harness.py --shim asmpython --matrix
    python conformance/harness.py --shim asmpython --groups pep,functions
    python conformance/harness.py --shim cpython            # must be 100%

A developer tool for asmpython, not a distributable package. The `cpython` shim
is the ORACLE (what regen derives expectations from and selftest validates
against); `asmpython` is the SUBJECT. Nothing here is asmpython-specific except
that one shim -- deliberately, because a suite that knew about asmpython could
be bent toward what asmpython already does.

Exit codes: 0 = every counted case passed; 1 = at least one counted failure.
`impl`-tier cases are run and reported but never counted (see README.md).
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from concurrent.futures import (
    ProcessPoolExecutor, ThreadPoolExecutor, as_completed,
)
from dataclasses import dataclass, field
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"
SHIMS = ROOT / "shims"

#: Tiers whose failures count against an implementation. `impl` is recorded but
#: excluded: those are CPython accidents -- refcount-driven collection timing,
#: small-int caching, address values -- not language guarantees. asmpython has
#: no obligation to reproduce them, and counting them would put permanent
#: failures in the score that no one should ever fix.
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


# --- merge mode -------------------------------------------------------------
#
# WHY IT EXISTS. Two things a case-per-process run cannot see:
#
#   * INTERACTION. Every case gets a fresh process, so anything the runtime
#     keeps for the length of one program is fresh too. A table that should be
#     per-run and is not, a name two cases both bind, a bundled module spliced
#     twice -- none of it can fail here, and all of it can fail in a real
#     program. Three bugs of exactly that shape were found by hand in one
#     session; this is the mode that would have found them.
#   * COST. A compile emits the whole object runtime -- three quarters of a
#     megabyte of C -- and `gcc` recompiles it per case. Forty cases in one
#     program is one such compile instead of forty.
#
# WHAT IT DOES NOT DO IS REWRITE THE CASES. `cases/` is the oracle, and a
# merged program built by renaming what a case declares would be testing
# something the suite does not contain. Batches are chosen so that renaming is
# unnecessary: cases whose module-level names are DISJOINT can be concatenated
# and each still means exactly what it meant alone.
#
# AND A BATCH IS NEVER THE LAST WORD. Its expected output is the concatenation
# of its cases' own expectations, so a mismatch says the batch failed and not
# which case -- so every failing batch is re-run case by case. A case that then
# passes ALONE is the interesting result: it is a divergence that only appears
# when programs share a process, which is the thing this mode is for.


def _module_names(path: Path) -> set:
    """Every name a case binds at MODULE level, or declares `global`.

    What two cases may not share if they are to be concatenated. Read off the
    tree rather than the text, because `del`, a `for` target and an `import x
    as y` all bind and none of them looks like an assignment.
    """
    import ast
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        # A case the ORACLE cannot parse is one this cannot reason about, so
        # it is never merged. `_batches` reads an empty set as "unmergeable".
        return set()
    out = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            out.add(stmt.name)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            for alias in stmt.names:
                out.add(alias.asname or alias.name.split(".")[0])
        else:
            for node in ast.walk(stmt):
                if isinstance(node, ast.Name) and isinstance(node.ctx,
                                                             ast.Store):
                    out.add(node.id)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                       ast.ClassDef)):
                    out.add(node.name)
    # A `global x` anywhere binds `x` at module level, however deep it is.
    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            out.update(node.names)
    return out


def _mergeable(case: Case) -> bool:
    """Whether a case may go in a batch at all.

    A case with NO EXPECTED OUTPUT contributes nothing to compare against, and
    one the oracle cannot parse cannot be reasoned about. Neither is a failure;
    both simply run alone.
    """
    return bool(case.expect.strip()) and not case.skip_note


#: Calls that ask WHAT IS BOUND rather than reading a name out of the
#: namespace, and the errors a case raises to find out.
_WATCHES = {"globals", "locals", "vars", "dir"}
_UNBOUND = {"NameError", "UnboundLocalError"}


def _observes_namespace(path: Path) -> bool:
    """Whether a case can tell what its neighbours bound.

    THE RULE THAT MAKES MOST CASES FREE TO MERGE: a case that passes ALONE
    never reads a module-level name before binding it -- if it did, CPython
    would raise NameError, and every case passes under CPython. Concatenation
    preserves order, so a neighbour rebinding `a` cannot change what a later
    case computes: the later case binds `a` before it reads it.

    That argument covers reading a name. It does NOT cover asking the namespace
    about itself -- `globals()`, a bare `dir()`, a `try/except NameError` whose
    whole point is whether a name is bound -- and it does not cover `del`,
    which is how a case makes a name unbound on purpose. Those cases need the
    namespace they had alone.

    1647 of 1675 mergeable cases are free by this test, which is the difference
    between packing 3x and packing 25x.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return True                     # unknown, so treated as observing
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _WATCHES:
                return True
        elif isinstance(node, ast.Name) and node.id in _UNBOUND:
            return True
        elif isinstance(node, ast.Delete):
            return True
    return False


def _batches(cases: list, size: int) -> tuple:
    """Pack cases into batches. Answers the batches and what runs alone.

    TWO POOLS, because the cases in them are safe for different reasons:

      * A case that does not observe the namespace packs with NO name
        constraint at all, for the reason `_observes_namespace` gives.
      * One that DOES keeps the conservative rule -- it may only share a batch
        with cases whose module-level names are disjoint from its own, so
        nothing it can see changed. Packed FIRST FIT ACROSS EVERY OPEN BATCH
        rather than only the newest: cases bind `a` and `xs` and `f`
        constantly, and packing into the newest alone gave batches of one and a
        half cases, which is barely a merge.

    The two pools never mix. A free case in an observer's batch is exactly the
    binding the observer would see.

    AND A MISJUDGEMENT HERE COSTS A RE-RUN, NOT A WRONG VERDICT. Whatever a
    batch does not settle is re-run case by case, so the worst a too-permissive
    rule can do is spend the time this mode was saving.
    """
    free, watched, alone = [], [], []
    for case in cases:
        if not _mergeable(case):
            alone.append(case)
        elif _observes_namespace(case.path):
            watched.append(case)
        else:
            free.append(case)

    batches = [free[i:i + size] for i in range(0, len(free), size)]

    #: (cases, names taken). Kept open until full, so a later case can land in
    #: an earlier one.
    open_batches: list = []
    for case in watched:
        names = _module_names(case.path)
        if not names and case.path.read_text(encoding="utf-8").strip():
            alone.append(case)          # unparsed by the oracle: never merged
            continue
        for held, taken in open_batches:
            if len(held) < size and not (names & taken):
                held.append(case)
                taken |= names
                break
        else:
            open_batches.append(([case], set(names)))
    batches.extend(held for held, _ in open_batches)
    return batches, alone


#: Printed between two cases in a merged program, so the output can be split
#: back into the segments a case-per-process run would have produced.
#:
#: A MARKER AND NOT A CONCATENATED EXPECTATION. The harness rstrips a case's
#: output before comparing, so a case ending in `print("")` has a trailing
#: blank line its `# expect:` block cannot hold -- true and harmless alone,
#: and wrong the moment another case's output follows it. Comparing segment by
#: segment applies the same rstrip in the same place, which is what makes a
#: merged verdict mean what a solo one means.
#:
#: NOT AN EDIT TO ANY CASE: it is a statement BETWEEN two of them, in the same
#: category as the `# --- <id>` comment that separates them.
_BOUNDARY = "@@asmpy-case-boundary@@"


def run_batch(batch: list, shim, timeout: int, work: Path) -> list:
    """Compile and run one batch. Answers a Result per case.

    A batch answers PASS for each case it settles and BATCH for all of them
    otherwise -- BATCH is not a verdict, it is "ask again one at a time", and
    the caller does.
    """
    merged = work / ("merged_" + str(abs(hash(batch[0].id)) % 10 ** 8)
                     + ".py")
    pieces = []
    for case in batch:
        pieces.append("# --- " + case.id + "\n"
                      + case.path.read_text(encoding="utf-8").rstrip("\n"))
        pieces.append("print(%r)" % _BOUNDARY)
    merged.write_text("\n".join(pieces) + "\n", encoding="utf-8")
    try:
        out, err, code = shim.run(str(merged), timeout)
    except TimeoutError:
        return [Result(c, "TIMEOUT", "batch exceeded " + str(timeout) + "s")
                for c in batch]
    except Exception as exc:
        return [Result(c, "ERROR", "shim raised: " + repr(exc)) for c in batch]
    if code != 0:
        # A REFUSAL OR A CRASH poisons everything after it, so nothing here is
        # settled and every case goes back to the queue.
        return [Result(c, "BATCH", (err or "").strip()[:300]) for c in batch]
    segments = out.split(_BOUNDARY + "\n")
    if len(segments) < len(batch):
        # A case stopped early, so the boundaries do not line up and no
        # segment can be trusted to belong to the case it looks like.
        return [Result(c, "BATCH", "output ended early") for c in batch]
    out_results = []
    for case, segment in zip(batch, segments):
        if _normalize(segment) == _normalize(case.expect):
            out_results.append(Result(case, "PASS"))
        else:
            out_results.append(Result(case, "BATCH", "differs in a batch"))
    return out_results


#: Marks used in the matrix view. Deliberately single-character so a wide grid
#: still fits, and deliberately distinct for REFUSED: "cannot compile this" and
#: "compiles to the wrong answer" are both non-conformance but different bugs,
#: and collapsing them loses the distinction that tells you where to look.
_MARK = {"PASS": ".", "FAIL": "X", "REFUSED": "C", "TIMEOUT": "T",
         "ERROR": "E", "SKIP": "-"}


#: Per-process shim cache. A shim is imported from a path at run time, so the
#: module object cannot be pickled and sent to a worker -- the NAME goes
#: instead and each worker imports its own copy once.
_SHIM_CACHE: dict = {}


def _run_case_in_process(case: Case, shim_name: str, timeout: int,
                         paranoid: bool) -> Result:
    """Worker entry point for `--exec process`. Must be module-level and
    picklable by reference, which rules out passing the loaded shim."""
    shim = _SHIM_CACHE.get(shim_name)
    if shim is None:
        shim = _SHIM_CACHE[shim_name] = load_shim(shim_name)
    return run_case(case, shim, timeout, paranoid)


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
    ap.add_argument("--exec", dest="exec_mode", default="thread",
                    choices=("thread", "process"),
                    help="run cases on threads (default) or separate "
                         "processes. Measured no faster on a 4-core machine -- "
                         "compiling saturates the cores and subprocess.run "
                         "releases the GIL anyway -- but the balance shifts "
                         "with core count.")
    ap.add_argument("--merge", type=int, default=0, metavar="N",
                    help="compile up to N cases into ONE program and run them "
                         "together. Catches what a case-per-process run "
                         "cannot -- state the runtime keeps for the length of "
                         "one program, names two cases share -- and costs one "
                         "compile per batch instead of one per case. A failing "
                         "batch is re-run case by case, so nothing is lost.")
    ap.add_argument("--matrix", action="store_true",
                    help="collapse generated cross-products into a grid")
    ap.add_argument("--json", metavar="PATH",
                    help="write the full per-case result as JSON")
    ap.add_argument("--live-src", action="store_true",
                    help="compile from src/ directly instead of a snapshot; "
                         "editing src/ mid-run then makes the score describe "
                         "a tree that never existed")
    args = ap.parse_args(argv)

    # A FROZEN COPY OF THE COMPILER, taken before the first case runs. This
    # matters more here than anywhere: a conformance run starts a fresh
    # process per case, so every case re-imports from disk, and a run takes
    # minutes. Editing `src/` in the middle mixes two compilers into one
    # score -- which has already cost a discarded measurement.
    frozen = None
    if not args.live_src and args.shim == "asmpython":
        import atexit
        import os
        root = Path(__file__).resolve().parents[1]
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from tests.harness import snapshot as _snap
        frozen = _snap.take(root, f"conf-{os.getpid()}")
        _snap.publish(frozen)
        atexit.register(_snap.discard, frozen)

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

    use_processes = args.exec_mode == "process" and args.jobs > 1
    how = "processes" if use_processes else "threads"
    print(f"conformance: {len(cases)} case(s), shim={args.shim}, "
          f"tiers={','.join(tiers)}, {args.jobs} {how}")
    results: list[Result] = []
    _batched_ids: set = set()

    if args.merge > 1:
        # MERGED FIRST, THEN WHAT IT COULD NOT SETTLE. A batch answers PASS
        # for all its cases or BATCH for all of them; the second is not a
        # verdict, it is "ask again one at a time", and the loop below does.
        batches, alone = _batches(cases, args.merge)
        merged_ok, unresolved = [], list(alone)
        #: Which cases a batch held and could not settle -- the population a
        #: merge-only divergence can come from.
        _batched_ids = set()
        work = Path(tempfile.mkdtemp(prefix="merged"))
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(run_batch, b, shim, args.timeout * 4, work)
                    for b in batches]
            for fut in as_completed(futs):
                for r in fut.result():
                    if r.status == "PASS":
                        merged_ok.append(r)
                    else:
                        unresolved.append(r.case)
                        _batched_ids.add(r.case.id)
        results.extend(merged_ok)
        cases = [c for c in unresolved if isinstance(c, Case)]
        print(f"  merged: {len(batches)} batch(es), "
              f"{len(merged_ok)} case(s) settled, {len(cases)} to re-run "
              f"alone", flush=True)

    if use_processes:
        # Run ONE case here first. An implementation shim may build a shared
        # artifact on its first compile (asmpython builds its runtime archive
        # lazily), and the lock that stops two THREADS racing to build it does
        # not span processes -- so without this, N workers would all find it
        # missing at once and build into the same directory. Threads keep using
        # the shim's own lock and need no warm-up.
        results.append(run_case(cases[0], shim, args.timeout, args.paranoid))
        remaining = cases[1:]
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            futs = [pool.submit(_run_case_in_process, c, args.shim,
                                args.timeout, args.paranoid)
                    for c in remaining]
            for fut in as_completed(futs):
                results.append(fut.result())
    else:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            futs = {pool.submit(run_case, c, shim, args.timeout, args.paranoid): c
                    for c in cases}
            for fut in as_completed(futs):
                results.append(fut.result())

    results.sort(key=lambda r: r.case.id)
    if args.merge > 1:
        # THE INTERESTING RESULT. A case that failed in a batch and passes
        # alone did not fail because of itself: something the runtime keeps
        # for the length of one program, or a name a neighbour bound, changed
        # its answer. That is exactly what merge mode exists to surface, and
        # it would otherwise be invisible -- the re-run says PASS and the
        # score never moves.
        merged_only = [r.case.id for r in results
                       if r.status == "PASS" and r.case.id in _batched_ids]
        if merged_only:
            print("")
            print("MERGE-ONLY DIVERGENCE: " + str(len(merged_only))
                  + " case(s) failed in a batch and pass alone.")
            for one in merged_only[:20]:
                print("   ", one)
            print("  These are INTERACTION bugs, not case failures. "
                  "The score below counts them as passes, which is "
                  "what they are on their own.")
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
