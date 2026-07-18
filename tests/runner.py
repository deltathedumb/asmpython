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
    out = BUILD / (stem + (".exe" if target == "windows" else ""))
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
    run = subprocess.run([str(out)], capture_output=True, text=True, input=stdin_data, **exe_flags)
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
    out = BUILD / case.stem
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
    target = _detect_target()
    mode = " (runtime-lib)" if _use_runtime_lib else ""
    print(f"asmpython test runner (target={target}, workers={workers}){mode}")

    tasks: list[tuple] = []
    if CASES.is_dir():
        for case in sorted(CASES.glob("*.py")):
            tasks.append((run_positive, case, target))
    if CASES_FAIL.is_dir():
        for case in sorted(CASES_FAIL.glob("*.py")):
            tasks.append((run_negative, case, target))

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
    for r in results:
        mark = "OK  " if r.ok else "FAIL"
        print(f"  [{mark}] {r.name}")
        if not r.ok:
            for line in r.detail.splitlines():
                print(f"        {line}")
    print(f"\n{len(results) - len(fails)}/{len(results)} passed")
    return 0 if not fails else 1


def _safe_run(fn, case: Path, target: str) -> TestResult:
    try:
        return fn(case, target)
    except Exception as exc:
        return TestResult(case.name, False, f"runner error: {exc}")


if __name__ == "__main__":
    raise SystemExit(main())
