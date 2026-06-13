#!/usr/bin/env python3
"""Self-compile asmpython for Windows and Linux."""

import os
import subprocess
import sys
from pathlib import Path

ROOT    = Path(__file__).parent.resolve()
SRC     = ROOT / "asmpython" / "__main__.py"
OUT_WIN = ROOT / "build" / "asmpython.exe"
OUT_LIN = ROOT / "build" / "asmpython-linux"

def base_env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return env

def run_windows() -> None:
    print(f"Self-hosting (windows): compiling asmpython -> {OUT_WIN}")
    r = subprocess.run(
        [sys.executable, "-m", "asmpython", str(SRC),
         "-o", str(OUT_WIN), "--target", "windows",
         "--nasm", "nasm", "--gcc", "gcc"],
        env=base_env(),
    )
    if r.returncode != 0:
        print("\nSelf-host build FAILED (windows).")
        sys.exit(1)
    print(f"Self-host build OK: {OUT_WIN}\n")

def run_linux() -> None:
    print(f"Self-hosting (linux): compiling asmpython -> {OUT_LIN}")
    # Convert Windows paths to WSL paths.
    def wsl_path(p: Path) -> str:
        r = subprocess.run(["wsl", "wslpath", "-u", str(p)],
                           capture_output=True, text=True)
        return r.stdout.strip()

    wsl_src  = wsl_path(SRC)
    wsl_out  = wsl_path(OUT_LIN)
    wsl_root = wsl_path(ROOT)

    r = subprocess.run([
        "wsl", "env",
        f"PYTHONPATH={wsl_root}",
        "python3", "-m", "asmpython",
        wsl_src, "-o", wsl_out, "--target", "linux",
        "--nasm", "/usr/bin/nasm", "--gcc", "/usr/bin/gcc",
    ])
    if r.returncode != 0:
        print("\nSelf-host build FAILED (linux).")
        sys.exit(1)
    print(f"Self-host build OK: {OUT_LIN}\n")

OUT_WIN.parent.mkdir(exist_ok=True)
run_windows()
run_linux()
