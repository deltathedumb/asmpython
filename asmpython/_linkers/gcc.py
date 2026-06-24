"""gcc linker: shells out to gcc to link object files into an executable.

The default linker for the legacy (NASM-text) backend, and usable by any
backend whose objects gcc's linker understands (COFF on Windows, ELF on
Linux -- which is every object format asmpython's backends emit).
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

requested_args: list[dict] = []


def link(ctx: dict) -> bytes:
    gcc = ctx.get("gcc_path") or "gcc"
    objects: list[bytes] = ctx["objects"]
    extra_args: list[str] = ctx.get("extra_args", [])

    with tempfile.TemporaryDirectory(prefix="asmpython_gcc_link_") as tmp:
        tmp_dir = Path(tmp)
        obj_suffix = ".obj" if ctx.get("target_os") == "windows" else ".o"
        obj_paths = []
        for i, data in enumerate(objects):
            p = tmp_dir / f"obj{i}{obj_suffix}"
            p.write_bytes(data)
            obj_paths.append(p)

        out_path = tmp_dir / ("a.exe" if ctx.get("target_os") == "windows" else "a.out")
        cmd = [str(gcc), *[str(p) for p in obj_paths], "-o", str(out_path), *extra_args]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            raise RuntimeError(f"gcc link failed (exit {proc.returncode}):\n{proc.stderr}")
        return out_path.read_bytes()
