"""Run CPython's official Lib/test modules against pyinbin.

This is a release-gate tool, not a replacement for the workspace runner. It
keeps a CPython baseline and an independent pyinbin result for the same module
set. ``--required`` makes any pyinbin failure fail the command, which is the
condition required before declaring 3.14 ready.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    detail: str = ""


def module_name(path: Path) -> str:
    return path.parent.name if path.name == "__main__.py" else path.stem


def discover(lib_root: Path, pattern: str, limit: int | None) -> list[Path]:
    test_root = lib_root / "test"
    if not test_root.is_dir():
        raise FileNotFoundError(f"CPython test package not found: {test_root}")
    paths: list[Path] = []
    for path in sorted(test_root.glob(pattern)):
        if path.is_file() and path.suffix == ".py":
            # ``test_*`` also matches non-module fixtures test modules load
            # as data (e.g. test_difflib_expect.html) -- only real Python
            # source is an importable module.
            paths.append(path)
        elif path.is_dir() and (path / "__main__.py").is_file():
            paths.append(path / "__main__.py")
    return paths[:limit] if limit is not None else paths


def run_cpython(paths: list[Path], lib_root: Path, jobs: int) -> int:
    names = [module_name(path) for path in paths]
    if not names:
        return 0
    proc = subprocess.run(
        [sys.executable, "-m", "test", "-j", str(jobs), *names],
        cwd=lib_root,
        text=True,
    )
    return proc.returncode


def run_pyinbin(path: Path, lib_root: Path, timeout: float) -> Result:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "asmpython",
            "pyinbin",
            "run",
            str(path),
            "--import-root",
            str(lib_root),
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode == 0:
        return Result(module_name(path), True)
    detail = (proc.stderr or proc.stdout).strip().splitlines()
    return Result(module_name(path), False, detail[-1] if detail else f"exit {proc.returncode}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--lib-root", type=Path, default=None,
        help="CPython stdlib root containing test/ (default: current interpreter)",
    )
    parser.add_argument("--pattern", default="test_*", help="test module glob under Lib/test")
    parser.add_argument("--limit", type=int, default=None, help="only process the first N discovered modules")
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--mode", choices=("pyinbin", "cpython", "both"), default="pyinbin")
    parser.add_argument("--required", action="store_true", help="fail unless every pyinbin module passes")
    args = parser.parse_args(argv)

    lib_root = args.lib_root or Path(sysconfig_path())
    paths = discover(lib_root, args.pattern, args.limit)
    print(f"CPython official tests: {len(paths)} module(s) from {lib_root / 'test'}")
    if args.mode in ("cpython", "both"):
        code = run_cpython(paths, lib_root, args.jobs)
        if code:
            return code
    if args.mode in ("pyinbin", "both"):
        results: list[Result] = []
        for path in paths:
            try:
                result = run_pyinbin(path, lib_root, args.timeout)
            except subprocess.TimeoutExpired:
                result = Result(path.name, False, "timeout")
            results.append(result)
            print(f"  [{'OK' if result.ok else 'FAIL'}] {result.name}" + (f": {result.detail}" if result.detail else ""))
        failed = [result for result in results if not result.ok]
        print(f"pyinbin conformance: {len(results) - len(failed)}/{len(results)} passed")
        if failed and args.required:
            return 1
    return 0


def sysconfig_path() -> str:
    import sysconfig

    return sysconfig.get_path("stdlib")


if __name__ == "__main__":
    raise SystemExit(main())
