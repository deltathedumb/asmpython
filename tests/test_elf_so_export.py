from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze
from asmpython._compiler.ssa import ir_lower
from asmpython._backends.x86_64 import __module_backend__ as backend
from asmpython._backends.x86_64.elf_linker import link_elf

_DLOPEN_TEST_C = """
#include <dlfcn.h>
#include <stdio.h>

int main(void) {
    void *handle = dlopen("./libprobe.so", RTLD_NOW);
    if (!handle) { fprintf(stderr, "dlopen failed: %s\\n", dlerror()); return 1; }
    long (*add)(long, long) = (long (*)(long, long))dlsym(handle, "add");
    if (!add) { fprintf(stderr, "dlsym failed: %s\\n", dlerror()); return 1; }
    long result = add(19, 23);
    printf("%ld\\n", result);
    return result == 42 ? 0 : 1;
}
"""


def _wsl_distro() -> str | None:
    """First available WSL distro name, or None if wsl.exe / a distro / gcc isn't."""
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if wsl is None:
        return None
    try:
        listed = subprocess.run(
            [wsl, "-l", "-q"], capture_output=True, timeout=15
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if listed.returncode != 0:
        return None
    names = [
        line.strip() for line in listed.stdout.decode("utf-16-le", errors="ignore").splitlines()
        if line.strip()
    ]
    for name in names:
        probe = subprocess.run(
            [wsl, "-d", name, "--", "which", "gcc"], capture_output=True, timeout=15
        )
        if probe.returncode == 0:
            return name
    return None


_DISTRO = _wsl_distro()


@unittest.skipUnless(
    _DISTRO is not None, "requires WSL with a gcc-capable Linux distro to dlopen a real .so"
)
class ElfSoExportTests(unittest.TestCase):
    def test_public_function_exports_and_computes_correctly_via_dlopen(self) -> None:
        # Regression coverage for the whole @access(Public) -> real,
        # dlopen()-able, dlsym()-callable Linux .so export chain: parser
        # capture -> ir_lower.py reachability roots -> IRModule.exports ->
        # elf_linker.link_elf's ET_DYN + .dynsym DEFINED (SHN_ABS) entries.
        from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path

        source = "def add(left: int, right: int) -> int:\n    return left + right\n"
        module = Parser(Lexer(source).tokenize(), frozenset()).parse()
        for function in module.funcs:
            if function.name == "add":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

        ir_module = ir_lower.lower_module(module)
        self.assertEqual(ir_module.exports, ["add"])

        compiled = backend.compile(ir_module, {"target_os": "linux", "abi": "sysv"})
        program_object = next(iter(compiled.values()))
        shim_object = build_abi_shims("linux").read_bytes()
        build_runtime("linux")
        runtime_object = runtime_object_path("linux").read_bytes()

        so_bytes = link_elf(
            [program_object, shim_object, runtime_object],
            is_library=True,
            exports=["add"],
            soname="libprobe.so",
        )

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "libprobe.so").write_bytes(so_bytes)
            (tmp_path / "test_dlopen.c").write_text(_DLOPEN_TEST_C, encoding="utf-8")

            wsl_dir = self._to_wsl_path(tmp_path)
            wsl = shutil.which("wsl.exe") or shutil.which("wsl")
            build = subprocess.run(
                [wsl, "-d", _DISTRO, "--", "bash", "-c",
                 f"cd '{wsl_dir}' && gcc -o test_dlopen test_dlopen.c -ldl"],
                capture_output=True, timeout=30,
            )
            self.assertEqual(build.returncode, 0, build.stderr.decode(errors="replace"))
            run = subprocess.run(
                [wsl, "-d", _DISTRO, "--", "bash", "-c",
                 f"cd '{wsl_dir}' && ./test_dlopen"],
                capture_output=True, timeout=30,
            )
            self.assertEqual(
                run.returncode, 0,
                f"stdout={run.stdout!r} stderr={run.stderr!r}",
            )
            self.assertEqual(run.stdout.decode().strip(), "42")

    @staticmethod
    def _to_wsl_path(path: Path) -> str:
        drive = path.drive.rstrip(":").lower()
        rest = path.as_posix()[len(path.drive) + 1:]
        return f"/mnt/{drive}/{rest}"


if __name__ == "__main__":
    unittest.main()
