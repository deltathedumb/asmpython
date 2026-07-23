from __future__ import annotations

import ctypes
import shutil
import sys
import unittest
from pathlib import Path

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze
from asmpython._compiler import ir_lower
from asmpython._backends.x86_64 import __module_backend__ as backend
from asmpython._backends.x86_64.pe_linker import link_pe


def _toolchain_available() -> bool:
    return sys.platform == "win32" and shutil.which("nasm") is not None


@unittest.skipUnless(
    _toolchain_available(), "requires Windows + nasm to build/load a real PE DLL"
)
class PeDllExportTests(unittest.TestCase):
    def test_public_function_exports_and_computes_correctly_via_ctypes(self) -> None:
        # Regression coverage for the whole @access(Public) -> real,
        # loadable, callable Windows DLL export chain: parser capture ->
        # ir_lower.py reachability roots -> IRModule.exports ->
        # pe_linker.link_pe's PE export directory.
        from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path

        source = "def add(left: int, right: int) -> int:\n    return left + right\n"
        module = Parser(Lexer(source).tokenize(), frozenset()).parse()
        for function in module.funcs:
            if function.name == "add":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

        ir_module = ir_lower.lower_module(module)
        self.assertEqual(ir_module.exports, ["add"])

        compiled = backend.compile(ir_module, {"target_os": "windows", "abi": "win64"})
        program_object = next(iter(compiled.values()))
        shim_object = build_abi_shims("windows").read_bytes()
        build_runtime("windows")
        runtime_object = runtime_object_path("windows").read_bytes()

        dll_bytes = link_pe(
            [program_object, shim_object, runtime_object],
            is_library=True,
            exports=["add"],
        )

        import os
        import tempfile

        fd, path_str = tempfile.mkstemp(suffix=".dll")
        dll_path = Path(path_str)
        with os.fdopen(fd, "wb") as stream:
            stream.write(dll_bytes)

        library = ctypes.WinDLL(str(dll_path))
        try:
            library.add.restype = ctypes.c_int64
            library.add.argtypes = [ctypes.c_int64, ctypes.c_int64]
            self.assertEqual(library.add(19, 23), 42)
        finally:
            # Windows keeps a loaded DLL's file locked -- FreeLibrary before
            # attempting to delete it, or the unlink below raises
            # PermissionError even though the actual test already passed.
            ctypes.windll.kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.FreeLibrary(library._handle)
            dll_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
