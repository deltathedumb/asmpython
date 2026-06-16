#!/usr/bin/env python3
"""Self-compile asmpython for Windows and Linux."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
SRC = ROOT / "asmpython" / "__main__.py"
OUT_WIN = ROOT / "build" / "asmpython.exe"
OUT_LIN = ROOT / "build" / "asmpython-linux"


def base_env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def run_windows() -> None:
    print(f"Self-hosting (windows): compiling asmpython -> {OUT_WIN}")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "asmpython",
            str(SRC),
            "-o",
            str(OUT_WIN),
            "--target",
            "windows",
            "--nasm",
            "nasm",
            "--gcc",
            "gcc",
        ],
        env=base_env(),
    )
    if r.returncode != 0:
        print("\nSelf-host build FAILED (windows).")
        sys.exit(1)
    print(f"Self-host build OK: {OUT_WIN}\n")


def run_linux() -> None:
    print(f"Self-hosting (linux): compiling asmpython -> {OUT_LIN}")

    # Convert Windows paths to WSL paths.  Returns None when WSL is
    # unavailable or the conversion fails so callers can skip gracefully.
    def wsl_path(p: Path) -> str | None:
        try:
            r = subprocess.run(
                ["wsl", "wslpath", "-u", str(p)],
                capture_output=True, text=True,
            )
            val = r.stdout.strip()
            return val if (r.returncode == 0 and val) else None
        except (FileNotFoundError, OSError, ValueError):
            return None

    wsl_src = wsl_path(SRC)
    if wsl_src is None:
        print("WSL not available or wslpath failed — skipping Linux self-host build.\n")
        return
    wsl_out = wsl_path(OUT_LIN)
    wsl_root = wsl_path(ROOT)
    if not wsl_out or not wsl_root:
        print("WSL path conversion failed — skipping Linux self-host build.\n")
        return

    r = subprocess.run([
        "wsl",
        "env",
        f"PYTHONPATH={wsl_root}",
        "python3",
        "-m",
        "asmpython",
        wsl_src,
        "-o",
        wsl_out,
        "--target",
        "linux",
        "--nasm",
        "/usr/bin/nasm",
        "--gcc",
        "/usr/bin/gcc",
    ])
    if r.returncode != 0:
        print("\nSelf-host build FAILED (linux).")
        sys.exit(1)
    print(f"Self-host build OK: {OUT_LIN}\n")


OUT_WIN.parent.mkdir(exist_ok=True)
run_windows()
run_linux()

# Check if we are in a batch script
if not os.getenv("ASMPYTHON_BUILD_BATCH") == "1" and sys.stdin.isatty():
    input("Press Enter to exit . . .")
