"""Exit-code-aware correctness sweep for a specific asmpython backend.

Unlike :mod:`tests.runner`, this tool is intended for backend parity work. It
runs only positive ``tests/cases`` programs, forces native compilation by
default, distinguishes build failures from runtime crashes and output
mismatches, honors ``# stdin:`` and ``# ext:`` metadata, and can emit a JSON
report suitable for comparing checkpoints.

Typical 3.14 parity sweep::

    python -m tests.backend_correctness --backend x86-64 \
        --no-pyinbin-fallback --json tests/backend-correctness.json

Use ``--pattern`` repeatedly to narrow the run while investigating a cluster::

    python -m tests.backend_correctness --pattern '*tuple*' \
        --pattern '*comprehension*' -j 2
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "cases"
DEFAULT_BUILD = Path("C:/Temp") if sys.platform == "win32" else ROOT / "tests" / "_backend_correctness"

OK = "OK"
MISMATCH = "MISMATCH"
CRASH = "CRASH"
TIMEOUT = "TIMEOUT"
BUILD_FAIL = "BUILD_FAIL"
BUILD_TIMEOUT = "BUILD_TIMEOUT"
INVALID = "INVALID"


@dataclass(slots=True)
class CaseResult:
    name: str
    status: str
    seconds: float
    detail: str = ""
    compile_returncode: int | None = None
    run_returncode: int | None = None

    @property
    def ok(self) -> bool:
        return self.status == OK


_METADATA_MARKERS = ("expect", "expect-error", "stdin", "ext")


def _is_metadata_marker(line: str) -> bool:
    return any(
        line == f"# {name}" or line.startswith(f"# {name}:")
        for name in _METADATA_MARKERS
    )


def _parse_block(src: str, marker: str) -> str | None:
    """Parse one leading metadata block without consuming the next marker."""
    lines: list[str] = []
    collecting = False
    prefix = f"# {marker}"
    for raw in src.splitlines():
        stripped = raw.strip()
        if not collecting:
            if not (stripped == prefix or stripped.startswith(prefix + ":")):
                continue
            collecting = True
            rest = stripped[len(prefix):].strip()
            if rest.startswith(":"):
                rest = rest[1:].strip()
            if rest:
                lines.append(rest)
            continue
        if not stripped.startswith("#") or _is_metadata_marker(stripped):
            break
        rest = stripped.lstrip("#")
        if rest.startswith(" "):
            rest = rest[1:]
        lines.append(rest)
    if not collecting:
        return None
    return "\n".join(lines)


def _parse_stdin(src: str) -> str:
    block = _parse_block(src, "stdin")
    return "" if block is None else block + "\n"


def _parse_extensions(src: str) -> list[str]:
    for raw in src.splitlines():
        stripped = raw.strip()
        if stripped.startswith("# ext:"):
            return [name.strip() for name in stripped[6:].split(",") if name.strip()]
    return []


def _normalize_output(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n"))


def _output_diff(expected: str, actual: str) -> str:
    lines = difflib.unified_diff(
        expected.splitlines(),
        actual.splitlines(),
        fromfile="expected",
        tofile="actual",
        lineterm="",
    )
    return "\n".join(lines)


def _target_default() -> str:
    return "windows" if sys.platform == "win32" else "linux"


def _safe_output_name(case: Path, target: str) -> str:
    stem = re.sub(r"(?i)(update|install|setup|patch)", "test", case.stem)
    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem)
    suffix = ".exe" if target == "windows" else ""
    return f"apbc_{stem}{suffix}"


def _compiler_command(
    case: Path,
    output: Path,
    *,
    target: str,
    backend: str,
    no_pyinbin_fallback: bool,
    use_runtime_lib: bool,
    extensions: Iterable[str],
) -> list[str]:
    cmd = [
        sys.executable,
        "-m",
        "asmpython",
        str(case),
        "--target",
        target,
        "--backend",
        backend,
        "-o",
        str(output),
    ]
    if no_pyinbin_fallback:
        cmd.append("--no-pyinbin-fallback")
    if use_runtime_lib:
        cmd.append("--use-runtime-lib")
    for extension in extensions:
        cmd.extend(("--ext", extension))
    return cmd


def run_case(
    case: Path,
    *,
    target: str,
    backend: str,
    build_dir: Path,
    compile_timeout: float,
    run_timeout: float,
    no_pyinbin_fallback: bool,
    use_runtime_lib: bool,
    keep_builds: bool,
) -> CaseResult:
    started = time.monotonic()
    try:
        src = case.read_text(encoding="utf-8")
    except OSError as exc:
        return CaseResult(case.name, INVALID, time.monotonic() - started, f"read failed: {exc}")

    expected_raw = _parse_block(src, "expect")
    if expected_raw is None:
        return CaseResult(case.name, INVALID, time.monotonic() - started, "no `# expect:` block found")

    build_dir.mkdir(parents=True, exist_ok=True)
    output = build_dir / _safe_output_name(case, target)
    command = _compiler_command(
        case,
        output,
        target=target,
        backend=backend,
        no_pyinbin_fallback=no_pyinbin_fallback,
        use_runtime_lib=use_runtime_lib,
        extensions=_parse_extensions(src),
    )

    try:
        compiled = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=compile_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _timeout_detail("compile", exc)
        return CaseResult(case.name, BUILD_TIMEOUT, time.monotonic() - started, detail)
    except OSError as exc:
        return CaseResult(case.name, BUILD_FAIL, time.monotonic() - started, f"compiler launch failed: {exc}")

    if compiled.returncode != 0:
        detail = _process_detail(compiled.stdout, compiled.stderr)
        return CaseResult(
            case.name,
            BUILD_FAIL,
            time.monotonic() - started,
            detail,
            compile_returncode=compiled.returncode,
        )

    creationflags = 0x08000000 if sys.platform == "win32" else 0
    try:
        executed = subprocess.run(
            [str(output)],
            cwd=ROOT,
            input=_parse_stdin(src),
            capture_output=True,
            text=True,
            timeout=run_timeout,
            creationflags=creationflags,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _timeout_detail("run", exc)
        result = CaseResult(
            case.name,
            TIMEOUT,
            time.monotonic() - started,
            detail,
            compile_returncode=compiled.returncode,
        )
    except OSError as exc:
        result = CaseResult(
            case.name,
            CRASH,
            time.monotonic() - started,
            f"program launch failed: {exc}",
            compile_returncode=compiled.returncode,
        )
    else:
        if executed.returncode != 0:
            result = CaseResult(
                case.name,
                CRASH,
                time.monotonic() - started,
                _process_detail(executed.stdout, executed.stderr),
                compile_returncode=compiled.returncode,
                run_returncode=executed.returncode,
            )
        else:
            expected = _normalize_output(expected_raw)
            actual = _normalize_output(executed.stdout)
            if actual != expected:
                result = CaseResult(
                    case.name,
                    MISMATCH,
                    time.monotonic() - started,
                    _output_diff(expected, actual),
                    compile_returncode=compiled.returncode,
                    run_returncode=executed.returncode,
                )
            else:
                result = CaseResult(
                    case.name,
                    OK,
                    time.monotonic() - started,
                    compile_returncode=compiled.returncode,
                    run_returncode=executed.returncode,
                )
    finally:
        if not keep_builds:
            try:
                output.unlink(missing_ok=True)
            except OSError:
                pass

    return result


def _timeout_detail(stage: str, exc: subprocess.TimeoutExpired) -> str:
    stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
    stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    details = _process_detail(stdout, stderr)
    return f"{stage} timed out after {exc.timeout}s" + (f"\n{details}" if details else "")


def _process_detail(stdout: str, stderr: str) -> str:
    chunks: list[str] = []
    if stderr:
        chunks.append("--- stderr ---\n" + stderr.rstrip())
    if stdout:
        chunks.append("--- stdout ---\n" + stdout.rstrip())
    return "\n".join(chunks)


def discover_cases(patterns: Iterable[str], limit: int | None = None) -> list[Path]:
    found: dict[str, Path] = {}
    for pattern in patterns:
        for case in CASES.glob(pattern):
            if case.is_file() and case.suffix == ".py":
                found[case.name] = case
    cases = [found[name] for name in sorted(found)]
    return cases if limit is None else cases[:limit]


def _write_json(path: Path, args: argparse.Namespace, results: list[CaseResult]) -> None:
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    payload = {
        "backend": args.backend,
        "target": args.target,
        "patterns": args.pattern,
        "counts": counts,
        "total": len(results),
        "results": [asdict(result) for result in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="x86-64", help="backend passed to asmpython")
    parser.add_argument("--target", default=_target_default(), choices=("windows", "linux"))
    parser.add_argument("--pattern", action="append", help="tests/cases glob; repeatable")
    parser.add_argument("--limit", type=int, help="run only the first N discovered cases")
    parser.add_argument("-j", "--jobs", type=int, default=min(os.cpu_count() or 4, 8))
    parser.add_argument("--compile-timeout", type=float, default=120.0)
    parser.add_argument("--run-timeout", type=float, default=10.0)
    parser.add_argument("--build-dir", type=Path, default=DEFAULT_BUILD)
    parser.add_argument("--json", type=Path, help="write a machine-readable result report")
    parser.add_argument("--keep-builds", action="store_true")
    parser.add_argument("--use-runtime-lib", action="store_true")
    parser.add_argument(
        "--no-pyinbin-fallback",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="force native-only compilation (default: enabled)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")
    if args.compile_timeout <= 0 or args.run_timeout <= 0:
        parser.error("timeouts must be positive")

    args.pattern = args.pattern or ["*.py"]
    cases = discover_cases(args.pattern, args.limit)
    if not cases:
        parser.error("no tests/cases files matched the requested pattern(s)")

    print(
        f"asmpython backend correctness sweep "
        f"(backend={args.backend}, target={args.target}, cases={len(cases)}, jobs={args.jobs})"
    )

    results_by_name: dict[str, CaseResult] = {}
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = {
            pool.submit(
                run_case,
                case,
                target=args.target,
                backend=args.backend,
                build_dir=args.build_dir,
                compile_timeout=args.compile_timeout,
                run_timeout=args.run_timeout,
                no_pyinbin_fallback=args.no_pyinbin_fallback,
                use_runtime_lib=args.use_runtime_lib,
                keep_builds=args.keep_builds,
            ): case.name
            for case in cases
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                results_by_name[name] = future.result()
            except Exception as exc:  # keep one harness bug from aborting the sweep
                results_by_name[name] = CaseResult(name, INVALID, 0.0, f"harness error: {exc}")

    results = [results_by_name[case.name] for case in cases]
    for result in results:
        print(f"  [{result.status:<13}] {result.name} ({result.seconds:.2f}s)")
        if not result.ok and result.detail:
            for line in result.detail.splitlines():
                print(f"        {line}")

    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    summary_order = (OK, MISMATCH, CRASH, TIMEOUT, BUILD_FAIL, BUILD_TIMEOUT, INVALID)
    summary = " ".join(f"{name}={counts.get(name, 0)}" for name in summary_order)
    print(f"\n{summary} TOTAL={len(results)}")

    if args.json is not None:
        _write_json(args.json, args, results)
        print(f"report: {args.json}")

    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
