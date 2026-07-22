"""GCC linker implementation for ASMPython object files."""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

requested_args: list[dict] = []
production_suitable = True


def _sanitizer_flags(ctx: dict) -> list[str]:
    selected = tuple(ctx.get("sanitizers", ()))
    if not selected:
        return []
    if "memory" in selected:
        raise NotImplementedError(
            "the memory sanitizer requires a Clang-compatible linker/toolchain; "
            "GCC cannot honor --sanitize memory"
        )
    mapped: list[str] = []
    for name in selected:
        if name == "integer":
            if "undefined" not in mapped:
                mapped.append("undefined")
        else:
            mapped.append(name)
    return [f"-fsanitize={','.join(dict.fromkeys(mapped))}", "-fno-omit-frame-pointer"]


def link(ctx: dict) -> bytes:
    gcc = ctx.get("gcc_path") or "gcc"
    objects: list[bytes] = ctx["objects"]
    extra_args: list[str] = list(ctx.get("extra_args", []))
    sanitizer_args = _sanitizer_flags(ctx)

    with tempfile.TemporaryDirectory(prefix="asmpython_gcc_link_") as tmp:
        tmp_dir = Path(tmp)
        obj_suffix = ".obj" if ctx.get("target_os") == "windows" else ".o"
        obj_paths = []
        for index, data in enumerate(objects):
            path = tmp_dir / f"obj{index}{obj_suffix}"
            path.write_bytes(data)
            obj_paths.append(path)

        out_path = tmp_dir / ("a.exe" if ctx.get("target_os") == "windows" else "a.out")
        cmd = [
            str(gcc),
            *[str(path) for path in obj_paths],
            *sanitizer_args,
            "-o",
            str(out_path),
            *extra_args,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.stdout:
            sys.stdout.write(proc.stdout)
        if proc.returncode != 0:
            raise RuntimeError(f"gcc link failed (exit {proc.returncode}):\n{proc.stderr}")
        return out_path.read_bytes()
