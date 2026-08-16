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


def _parse(source: str):
    return Parser(Lexer(source).tokenize(), frozenset()).parse()


class Int8ParserTests(unittest.TestCase):
    def test_outparam_int8_and_inparam_int8_parse(self) -> None:
        module = _parse(
            "def f(src: inparam[int8], dst: outparam[int8]) -> int:\n"
            "    dst[0] = src[0]\n"
            "    return 0\n"
        )
        self.assertEqual(
            module.funcs[0].param_types, [("inparam", "int8"), ("outparam", "int8")]
        )


class Int8SemaTests(unittest.TestCase):
    def test_byte_copy_loop_is_accepted(self) -> None:
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def copy_bytes(src: inparam[int8], dst: outparam[int8], count: int) -> int:\n"
            "    i = 0\n"
            "    while i < count:\n"
            "        dst[i] = src[i]\n"
            "        i = i + 1\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "copy_bytes":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())


def _toolchain_available() -> bool:
    return sys.platform == "win32" and shutil.which("nasm") is not None


@unittest.skipUnless(
    _toolchain_available(), "requires Windows + nasm to build/load a real PE DLL"
)
class Int8DllTests(unittest.TestCase):
    def test_byte_buffer_copy_via_real_ctypes_uint8_arrays(self) -> None:
        from asmpython._backends.x86_64 import __module_backend__ as backend
        from asmpython._backends.x86_64.pe_linker import link_pe
        from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path

        module = _parse(
            "def copy_bytes(src: inparam[int8], dst: outparam[int8], count: int) -> int:\n"
            "    i = 0\n"
            "    while i < count:\n"
            "        dst[i] = src[i]\n"
            "        i = i + 1\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "copy_bytes":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())
        ir_module = ir_lower.lower_module(module)

        compiled = backend.compile(ir_module, {"target_os": "windows", "abi": "win64"})
        program_object = next(iter(compiled.values()))
        shim_object = build_abi_shims("windows").read_bytes()
        build_runtime("windows")
        runtime_object = runtime_object_path("windows").read_bytes()

        dll_bytes = link_pe(
            [program_object, shim_object, runtime_object],
            is_library=True,
            exports=["copy_bytes"],
        )

        import tempfile
        import os

        fd, path_str = tempfile.mkstemp(suffix=".dll")
        dll_path = Path(path_str)
        with os.fdopen(fd, "wb") as stream:
            stream.write(dll_bytes)

        library = ctypes.WinDLL(str(dll_path))
        try:
            library.copy_bytes.restype = ctypes.c_int64
            library.copy_bytes.argtypes = [
                ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint8), ctypes.c_int64
            ]
            src_buf = (ctypes.c_uint8 * 5)(1, 2, 3, 4, 5)
            dst_buf = (ctypes.c_uint8 * 5)(0, 0, 0, 0, 0)
            status = library.copy_bytes(src_buf, dst_buf, 5)
            self.assertEqual(status, 0)
            self.assertEqual(list(dst_buf), [1, 2, 3, 4, 5])
        finally:
            ctypes.windll.kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.FreeLibrary(library._handle)
            dll_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
