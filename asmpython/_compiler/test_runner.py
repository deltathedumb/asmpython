"""Differential ``asmpython test`` runner.

The command can execute each discovered script through CPython, PyinBin, the
native compiler, and the normal hybrid compiler path, then compare observable
exit status/stdout/stderr against the CPython baseline.
"""
from __future__ import annotations

import argparse
import contextlib
import difflib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class EngineResult:
    engine: str
    source: str
    returncode: int
    stdout: str
    stderr: str
    duration_seconds: float
    artifact: str | None = None
    phase: str = "run"
    skipped: bool = False

    @property
    def passed(self) -> bool:
        return not self.skipped and self.returncode == 0


def _discover(path: Path, pattern: str) -> list[Path]:
    if path.is_file():
        return [path.resolve()]
    if not path.is_dir():
        raise FileNotFoundError(path)
    found = sorted(item.resolve() for item in path.rglob(pattern) if item.is_file())
    if pattern == "test*.py":
        found.extend(
            item.resolve() for item in path.rglob("*_test.py")
            if item.is_file() and item.resolve() not in found
        )
    return sorted(set(found))


def _subprocess_result(engine: str, source: Path, command: list[str], *, cwd: Path | None = None, phase: str = "run") -> EngineResult:
    started = time.perf_counter()
    process = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        text=True,
        capture_output=True,
        env={**os.environ, "PYTHONUTF8": "1", "ASMPYTHON_TEST_CHILD": "1"},
    )
    return EngineResult(
        engine=engine,
        source=str(source),
        returncode=process.returncode,
        stdout=process.stdout,
        stderr=process.stderr,
        duration_seconds=time.perf_counter() - started,
        phase=phase,
    )


def _run_cpython(source: Path) -> EngineResult:
    return _subprocess_result("cpython", source, [sys.executable, str(source)], cwd=source.parent)


def _run_pyinbin(source: Path) -> EngineResult:
    from asmpython.pyinbin import run_source

    stdout = io.StringIO()
    stderr = io.StringIO()
    started = time.perf_counter()
    returncode = 0
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            run_source(source)
    except BaseException as exc:
        returncode = 1
        try:
            from asmpython._runtime.mixed_traceback import format_mixed_exception
            stderr.write(format_mixed_exception(exc))
        except Exception:
            stderr.write(f"{type(exc).__name__}: {exc}\n")
    return EngineResult(
        engine="pyinbin",
        source=str(source),
        returncode=returncode,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        duration_seconds=time.perf_counter() - started,
    )


def _artifact_path(root: Path, source: Path, target: str) -> Path:
    name = source.stem
    if target == "windows":
        return root / f"{name}.exe"
    if target == "freestanding":
        return root / f"{name}.bin"
    if target == "freestanding16":
        return root / f"{name}.img"
    return root / name


def _build_and_run(
    engine: str,
    source: Path,
    root: Path,
    *,
    target: str,
    backend: str | None,
    native_only: bool,
) -> EngineResult:
    artifact = _artifact_path(root, source, target)
    command = [
        sys.executable, "-m", "asmpython", "build", str(source),
        "--target", target, "-o", str(artifact),
    ]
    if backend:
        command.extend(("--backend", backend))
    if native_only:
        command.append("--no-pyinbin-fallback")
    build = _subprocess_result(engine, source, command, cwd=source.parent, phase="build")
    build.artifact = str(artifact)
    if build.returncode != 0:
        return build
    if not artifact.is_file():
        # The hybrid path may have executed through PyinBin during the build and
        # intentionally produced no native artifact. Its captured output is the
        # observable program result in that case.
        build.phase = "hybrid-fallback"
        return build
    if target not in {"windows", "linux"}:
        build.skipped = True
        build.phase = "artifact-only"
        return build
    run = _subprocess_result(engine, source, [str(artifact)], cwd=source.parent)
    run.artifact = str(artifact)
    # Build chatter is intentionally not mixed into program output.
    if build.stderr:
        run.stderr = build.stderr + run.stderr
    return run


def _comparison(reference: EngineResult, candidate: EngineResult) -> dict[str, Any]:
    equal = (
        reference.returncode == candidate.returncode
        and reference.stdout == candidate.stdout
        and reference.stderr == candidate.stderr
    )
    stdout_diff = "".join(difflib.unified_diff(
        reference.stdout.splitlines(True), candidate.stdout.splitlines(True),
        fromfile="cpython.stdout", tofile=f"{candidate.engine}.stdout",
    ))
    stderr_diff = "".join(difflib.unified_diff(
        reference.stderr.splitlines(True), candidate.stderr.splitlines(True),
        fromfile="cpython.stderr", tofile=f"{candidate.engine}.stderr",
    ))
    return {
        "equal": equal,
        "returncode_equal": reference.returncode == candidate.returncode,
        "stdout_equal": reference.stdout == candidate.stdout,
        "stderr_equal": reference.stderr == candidate.stderr,
        "stdout_diff": stdout_diff,
        "stderr_diff": stderr_diff,
    }


def command_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="asmpython test",
        description="Run scripts through CPython, native ASMPython, PyinBin, and hybrid ASMPython.",
    )
    parser.add_argument("path", type=Path, nargs="?", default=Path("tests"))
    parser.add_argument("--pattern", default="test*.py")
    parser.add_argument(
        "--engine", choices=["all", "cpython", "native", "pyinbin", "hybrid"],
        action="append", default=None,
    )
    parser.add_argument("--target", default="windows" if sys.platform == "win32" else "linux")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--keep-builds", action="store_true")
    parser.add_argument("--no-compare", action="store_true")
    args = parser.parse_args(argv)

    try:
        sources = _discover(args.path, args.pattern)
    except OSError as exc:
        print(f"asmpython: test: {exc}", file=sys.stderr)
        return 2
    if not sources:
        print(f"asmpython: test: no tests matched {args.path} / {args.pattern}", file=sys.stderr)
        return 2

    requested = set(args.engine or ["all"])
    engines = ["cpython", "native", "pyinbin", "hybrid"] if "all" in requested else [
        engine for engine in ("cpython", "native", "pyinbin", "hybrid") if engine in requested
    ]
    need_reference = not args.no_compare and any(engine != "cpython" for engine in engines)
    if need_reference and "cpython" not in engines:
        engines.insert(0, "cpython")

    temporary = Path(tempfile.mkdtemp(prefix="asmpython-test-"))
    report: list[dict[str, Any]] = []
    failures = 0
    try:
        for index, source in enumerate(sources):
            case_root = temporary / f"{index:04d}-{source.stem}"
            case_root.mkdir(parents=True, exist_ok=True)
            results: dict[str, EngineResult] = {}
            for engine in engines:
                if engine == "cpython":
                    result = _run_cpython(source)
                elif engine == "pyinbin":
                    result = _run_pyinbin(source)
                elif engine == "native":
                    result = _build_and_run(
                        engine, source, case_root / engine,
                        target=args.target, backend=args.backend, native_only=True,
                    )
                else:
                    result = _build_and_run(
                        engine, source, case_root / engine,
                        target=args.target, backend=args.backend, native_only=False,
                    )
                results[engine] = result

            reference = results.get("cpython")
            comparisons: dict[str, Any] = {}
            case_failed = False
            for engine, result in results.items():
                if result.skipped:
                    continue
                if result.returncode != 0:
                    case_failed = True
                if reference is not None and engine != "cpython" and not args.no_compare:
                    comparison = _comparison(reference, result)
                    comparisons[engine] = comparison
                    if not comparison["equal"]:
                        case_failed = True
            failures += int(case_failed)
            report.append({
                "source": str(source),
                "failed": case_failed,
                "results": {engine: asdict(result) for engine, result in results.items()},
                "comparisons": comparisons,
            })

            if not args.json:
                print(f"{'FAIL' if case_failed else 'PASS'} {source}")
                for engine, result in results.items():
                    state = "SKIP" if result.skipped else str(result.returncode)
                    print(f"  {engine:<8} rc={state:<4} {result.duration_seconds:.3f}s ({result.phase})")
                for engine, comparison in comparisons.items():
                    if comparison["equal"]:
                        continue
                    print(f"  diff against {engine}:")
                    if comparison["stdout_diff"]:
                        print(comparison["stdout_diff"], end="")
                    if comparison["stderr_diff"]:
                        print(comparison["stderr_diff"], end="")
            if case_failed and args.fail_fast:
                break
    finally:
        if args.keep_builds:
            print(f"asmpython: test build directory: {temporary}", file=sys.stderr)
        else:
            shutil.rmtree(temporary, ignore_errors=True)

    if args.json:
        print(json.dumps({"tests": report, "failures": failures}, indent=2, sort_keys=True))
    else:
        print(f"{len(report)} test(s), {failures} failure(s)")
    return 1 if failures else 0
