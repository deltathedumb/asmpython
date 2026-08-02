from __future__ import annotations

import ctypes
import shutil
import sys
import unittest
from pathlib import Path

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze
from asmpython._compiler.ssa import ir_lower


def _toolchain_available() -> bool:
    return sys.platform == "win32" and shutil.which("nasm") is not None


@unittest.skipUnless(
    _toolchain_available(), "requires Windows + nasm to build/load a real PE DLL"
)
class LibraryScriptModelInitTests(unittest.TestCase):
    def test_library_with_no_explicit_main_still_initializes_module_globals(self) -> None:
        # Regression: a source with no explicit `def main():` -- e.g. a
        # pure C-ABI module whose only top-level statements are global
        # container initializers and @access(Public) functions, exactly
        # PortaPy's generated native API modules -- has ALL of its
        # module-level init code folded by ir_lower.py's lower_module into
        # a synthesized function literally named "main" (the "preserve the
        # script model" branch), not "__asmpy_module_init". An executable's
        # entry stub always calls "main" directly either way, so this was
        # invisible there, but a library's DllMain/DT_INIT_ARRAY only ever
        # looked for "__asmpy_module_init" -- so a library with no
        # explicit main() NEVER ran its own global initialization at all.
        # Confirmed via a real segfault (appending to an uninitialized
        # global list) while porting PortaPy's list_glue.c to Python.
        from asmpython._backends.x86_64 import __module_backend__ as backend
        from asmpython._backends.x86_64.pe_linker import link_pe
        from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path

        source = (
            "from asmpython import Public, access\n"
            "\n"
            "counter = [0]\n"
            "\n"
            "@access(Public)\n"
            "def get_counter() -> int:\n"
            "    return counter[0]\n"
        )
        module = Parser(Lexer(source).tokenize(), frozenset()).parse()
        for function in module.funcs:
            if function.name == "get_counter":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())
        self.assertFalse(any(f.name == "main" for f in module.funcs))

        ir_module = ir_lower.lower_module(module)
        self.assertIn("main", [f.name for f in ir_module.funcs])

        compiled = backend.compile(ir_module, {"target_os": "windows", "abi": "win64"})
        program_object = next(iter(compiled.values()))
        shim_object = build_abi_shims("windows").read_bytes()
        build_runtime("windows")
        runtime_object = runtime_object_path("windows").read_bytes()

        dll_bytes = link_pe(
            [program_object, shim_object, runtime_object],
            is_library=True,
            exports=["get_counter"],
        )

        import tempfile
        import os

        fd, path_str = tempfile.mkstemp(suffix=".dll")
        dll_path = Path(path_str)
        with os.fdopen(fd, "wb") as stream:
            stream.write(dll_bytes)

        library = ctypes.WinDLL(str(dll_path))
        try:
            library.get_counter.restype = ctypes.c_int64
            # Before the fix, `counter` was never initialized to [0] at
            # all -- reading it crashed (or, absent that specific crash
            # shape, would not reliably read back 0).
            self.assertEqual(library.get_counter(), 0)
        finally:
            ctypes.windll.kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.FreeLibrary(library._handle)
            dll_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
