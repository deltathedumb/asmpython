"""End-to-end test runner for asmpython.

Each `tests/cases/*.py` file is compiled, run, and its stdout compared against
the expected output declared in a leading `# expect:` block:

    # expect:
    # hello
    # 42

Negative tests (compiles must fail) live in `tests/cases_fail/*.py` and use
`# expect-error:` to assert a substring of the formatted error message:

    # expect-error: undefined variable 'x'

Run:    python -m tests.runner
Exit codes: 0 = all pass; 1 = at least one failure.
"""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "cases"
CASES_FAIL = ROOT / "tests" / "cases_fail"

# On Windows, build directly into C:\Temp (not a subdirectory) to avoid Smart
# App Control blocking freshly-compiled exes.  Fall back to the project-local
# _build directory on other platforms.
if sys.platform == "win32":
    BUILD = Path("C:/Temp")
else:
    BUILD = ROOT / "tests" / "_build"

# Artifact names are per-RUN, not just per-case.
#
# Every case used to compile to `BUILD/<case stem>`, and on Windows BUILD is
# the shared C:\Temp -- so two corpus runs at once (two agents, or one agent
# measuring while a background sweep finishes) wrote and then EXECUTED the
# same paths. Run A could compile a case, run B overwrite that exact file,
# and run A then execute run B's binary: a result attributed to the wrong
# tree, silently, with no error anywhere. That is the failure mode behind a
# spread of 645/677/682/715/716 on supposedly comparable trees, and it defeats
# worktree isolation completely, because the worktree isolates the SOURCE
# while the artifacts still collide.
#
# The tag must stay in the FILENAME rather than becoming a subdirectory:
# building directly into C:\Temp is what keeps Smart App Control from blocking
# freshly-compiled exes (see above), so a per-run subdirectory would trade one
# breakage for another.
_RUN_TAG = f"r{os.getpid():d}"


def _artifact(stem: str, target: str) -> Path:
    """Where this RUN's binary for `stem` goes. Unique per run; see _RUN_TAG."""
    return BUILD / f"{stem}_{_RUN_TAG}{'.exe' if target == 'windows' else ''}"


def _clean_run_artifacts() -> None:
    """Remove only THIS run's binaries, leaving a concurrent run's alone."""
    for leftover in BUILD.glob(f"*_{_RUN_TAG}*"):
        try:
            leftover.unlink()
        except OSError:
            pass  # still mapped by a just-exited process; harmless to leave

# Mutated by main() so subprocess calls pick it up.
_use_runtime_lib = False
# --backend/--no-pyinbin-fallback passthrough: previously silently ignored
# by this runner (only --use-runtime-lib and -j/--jobs were ever parsed),
# so `python -m tests.runner --backend x86-64` compiled every case with
# whatever asmpython._compiler.__main__'s own CLI default happened to be
# at the time -- confirmed as a real gap 2026-07-18 when the CLI default
# changed from legacy to x86-64 and this runner's "baseline" number
# silently changed to match, despite every session-long invocation
# explicitly passing --backend x86-64 (which had never actually reached
# the compiler subprocess). Both are None by default: an explicit
# --backend forwards as `--backend NAME`; --no-pyinbin-fallback forwards
# as that same bare flag; neither is forced when omitted, so the
# no-flags case still tracks whatever the CLI's own default is.
_backend: str | None = None
_no_pyinbin_fallback = False


@dataclass
class TestResult:
    name: str
    ok: bool
    detail: str = ""


def _parse_expect(src: str, marker: str) -> str | None:
    """Read a `# {marker}` block at the top of the file and join its lines."""
    lines: list[str] = []
    collecting = False
    for raw in src.splitlines():
        s = raw.strip()
        if not collecting:
            if s.startswith(f"# {marker}"):
                collecting = True
                rest = s[len(f"# {marker}"):].strip()
                if rest.startswith(":"):
                    rest = rest[1:].strip()
                if rest:
                    lines.append(rest)
            continue
        if s.startswith("#"):
            # Strip the leading `#` and a single conventional space, but
            # preserve any further leading whitespace — expected output may
            # itself start with spaces (e.g. right-justified formatting).
            rest = s.lstrip("#")
            if rest.startswith(" "):
                rest = rest[1:]
            lines.append(rest)
        else:
            break
    if not collecting:
        return None
    return "\n".join(lines)


def _parse_stdin(src: str) -> str:
    """Read a `# stdin:` block: lines fed to the program's stdin."""
    out = _parse_expect(src, "stdin")
    if out is None:
        return ""
    return out + "\n"


def _parse_ext(src: str) -> list[str]:
    """Read a `# ext: name1, name2` marker line and return the extension
    names to activate via `--ext`, one `--ext NAME` pair per name. A test
    case that needs an opt-in compiler-syntax extension (e.g. `constants`,
    for `const NAME = value`) declares it this way instead of an in-source
    `extend` directive -- activation is CLI-only, matching how a real
    invocation would enable it."""
    for raw in src.splitlines():
        s = raw.strip()
        if s.startswith("# ext:"):
            names = s[len("# ext:"):].strip()
            return [n.strip() for n in names.split(",") if n.strip()]
    return []


def _detect_target() -> str:
    if sys.platform == "win32":
        return "windows"
    return "linux"


def run_positive(case: Path, target: str) -> TestResult:
    expected = _parse_expect(case.read_text(encoding="utf-8"), "expect")
    if expected is None:
        return TestResult(case.name, False, "no `# expect:` block found")

    BUILD.mkdir(parents=True, exist_ok=True)
    # On Windows, strip UAC-triggering words ("update", "install", "setup",
    # "patch") from the output binary name to prevent installer-detection
    # heuristics from requiring elevation.
    stem = case.stem
    if sys.platform == "win32":
        import re as _re
        stem = _re.sub(r"(?i)(update|install|setup|patch)", "test", stem)
    out = _artifact(stem, target)
    src_text = case.read_text(encoding="utf-8")
    cmd = [sys.executable, "-m", "asmpython", str(case), "--target", target, "-o", str(out)]
    if _use_runtime_lib:
        cmd.append("--use-runtime-lib")
    if _backend is not None:
        cmd += ["--backend", _backend]
    if _no_pyinbin_fallback:
        cmd.append("--no-pyinbin-fallback")
    for ext_name in _parse_ext(src_text):
        cmd += ["--ext", ext_name]
    # On Windows, compile without creationflags (adding CREATE_NO_WINDOW to gcc
    # causes it to mark the output exe for UAC elevation).  Run the compiled exe
    # with CREATE_NO_WINDOW to suppress the UAC prompt.
    exe_flags: dict = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    cp = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if cp.returncode != 0:
        return TestResult(case.name, False, f"compile failed:\n{cp.stderr}{cp.stdout}")

    stdin_data = _parse_stdin(case.read_text(encoding="utf-8"))
    try:
        run = subprocess.run([str(out)], capture_output=True, text=True,
                             input=stdin_data, timeout=30, **exe_flags)
    except subprocess.TimeoutExpired:
        # A compiled program that never terminates (e.g. asmpython miscompiling
        # `x[::0]` into an infinite loop) must be a FAIL, not a hang that freezes
        # the whole suite. 30s is far beyond any legitimate test case's runtime.
        return TestResult(case.name, False, "program timed out (>30s, likely infinite loop)")
    if run.returncode != 0:
        return TestResult(case.name, False, f"program exited {run.returncode}\n{run.stderr}")
    got = "\n".join(l.rstrip() for l in run.stdout.replace("\r\n", "\n").rstrip("\n").split("\n"))
    expected_norm = "\n".join(l.rstrip() for l in expected.rstrip("\n").split("\n"))
    if got != expected_norm:
        return TestResult(case.name, False, _diff(expected_norm, got))
    return TestResult(case.name, True)


def run_negative(case: Path, target: str) -> TestResult:
    src_text = case.read_text(encoding="utf-8")
    expected = _parse_expect(src_text, "expect-error")
    if expected is None:
        return TestResult(case.name, False, "no `# expect-error:` block found")

    BUILD.mkdir(parents=True, exist_ok=True)
    # Same per-run tagging as run_positive: a negative case still writes an
    # output path, so it can still collide with a concurrent run.
    out = _artifact(case.stem, target)
    # --no-pyinbin-fallback: without it, the CLI's pyinbin fallback runs
    # after native compilation fails and its own (unrelated) error message
    # replaces the real, formatted native error in stderr -- these tests
    # exist specifically to check the *native compiler's* diagnostic text.
    cmd = [sys.executable, "-m", "asmpython", str(case), "--target", target,
           "--emit-asm", "--no-pyinbin-fallback", "-o", str(out)]
    if _use_runtime_lib:
        cmd.append("--use-runtime-lib")
    if _backend is not None:
        cmd += ["--backend", _backend]
    for ext_name in _parse_ext(src_text):
        cmd += ["--ext", ext_name]
    cp = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    if cp.returncode == 0:
        return TestResult(case.name, False, "expected compile to fail, but it succeeded")
    msg = (cp.stderr or "") + (cp.stdout or "")
    if expected not in msg:
        return TestResult(case.name, False, f"expected substring {expected!r} not found in:\n{msg}")
    return TestResult(case.name, True)


def _diff(expected: str, got: str) -> str:
    return f"--- expected ---\n{expected}\n--- got ---\n{got}\n--- end ---"


def main() -> int:
    global _use_runtime_lib, _backend, _no_pyinbin_fallback
    args = sys.argv[1:]
    _use_runtime_lib = "--use-runtime-lib" in args
    _no_pyinbin_fallback = "--no-pyinbin-fallback" in args
    # -j N or --jobs N overrides parallelism; default = CPU count (capped at 8)
    workers = min(os.cpu_count() or 4, 8)
    for i, a in enumerate(args):
        if a == "--backend" and i + 1 < len(args):
            _backend = args[i + 1]
        if a in ("-j", "--jobs") and i + 1 < len(args):
            try:
                workers = int(args[i + 1])
            except ValueError:
                pass
        elif a.startswith("-j") and len(a) > 2:
            try:
                workers = int(a[2:])
            except ValueError:
                pass
    # -k/--filter SUBSTR: run only cases whose filename contains SUBSTR.
    #
    # Verifying one fix used to cost a full ~1100-case run, so the cheapest way
    # to check a single case was to not check it. That is how a -10 regression
    # reached a commit. A substring filter makes checking one bucket a
    # seconds-long operation, which is the difference between verifying every
    # change and verifying the ones that seem worth the wait.
    case_filter = None
    for i, a in enumerate(args):
        if a in ("-k", "--filter") and i + 1 < len(args):
            case_filter = args[i + 1]
        elif a.startswith("-k") and len(a) > 2:
            case_filter = a[2:]

    target = _detect_target()
    mode = " (runtime-lib)" if _use_runtime_lib else ""
    if case_filter:
        mode += f" (filter={case_filter!r})"
    print(f"asmpython test runner (target={target}, workers={workers}){mode}")

    def _wanted(case: Path) -> bool:
        return case_filter is None or case_filter in case.name

    tasks: list[tuple] = []
    if CASES.is_dir():
        for case in sorted(CASES.glob("*.py")):
            if _wanted(case):
                tasks.append((run_positive, case, target))
    if CASES_FAIL.is_dir():
        for case in sorted(CASES_FAIL.glob("*.py")):
            if _wanted(case):
                tasks.append((run_negative, case, target))
    if not tasks:
        print(f"no cases matched filter {case_filter!r}")
        return 1

    # Run tests in parallel; collect results in submission order.
    result_map: dict[str, TestResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_name = {}
        for fn, case, tgt in tasks:
            fut = pool.submit(_safe_run, fn, case, tgt)
            future_to_name[fut] = case.name
        for fut in as_completed(future_to_name):
            result_map[future_to_name[fut]] = fut.result()

    # Re-sort results by original task order so output is deterministic.
    ordered_names = [case.name for _, case, _ in tasks]
    results = [result_map[n] for n in ordered_names]

    fails = [r for r in results if not r.ok]
    out_lines: list[str] = [f"asmpython test runner (target={target}, workers={workers}){mode}"]
    for r in results:
        mark = "OK  " if r.ok else "FAIL"
        line = f"  [{mark}] {r.name}"
        print(line)
        out_lines.append(line)
        if not r.ok:
            for detail_line in r.detail.splitlines():
                detail_out = f"        {detail_line}"
                print(detail_out)
                out_lines.append(detail_out)
    summary = f"\n{len(results) - len(fails)}/{len(results)} passed"
    print(summary)
    out_lines.append(summary)
    # Written every run (not just on failure) so a caller can read the full
    # pass/fail breakdown from disk instead of re-running the whole suite
    # just to see what changed -- avoids repeated multi-minute full-corpus
    # runs when only the summary or one file's detail is actually needed.
    # Only a FULL run may write results.txt. A filtered run's partial list
    # would look exactly like a full one to triage.py and to anyone diffing
    # failure sets, silently reporting every unrun case as "fixed".
    if case_filter is None:
        (ROOT / "results.txt").write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    else:
        print("(filtered run: results.txt left untouched)")
    # Only this run's binaries; a concurrent run's are left untouched. Without
    # this, C:\Temp accumulates one exe per case per run indefinitely (1045 had
    # piled up before the tagging above made them individually identifiable).
    _clean_run_artifacts()
    return 0 if not fails else 1


def _safe_run(fn, case: Path, target: str) -> TestResult:
    try:
        return fn(case, target)
    except Exception as exc:
        return TestResult(case.name, False, f"runner error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
