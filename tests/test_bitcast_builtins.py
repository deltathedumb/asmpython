from __future__ import annotations

import ctypes
import math
import shutil
import struct
import sys
import unittest
from pathlib import Path

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze, SemaError
from asmpython._compiler import ir_lower


def _parse(source: str):
    return Parser(Lexer(source).tokenize(), frozenset()).parse()


class BitcastCPythonBuiltinTests(unittest.TestCase):
    """bitcast_f2i/bitcast_i2f must be real names once `asmpython` is
    imported, since PortaPy's generated modules get imported directly (not
    just compiled) by its own test suite -- see asmpython/__init__.py."""

    def test_bitcast_f2i_matches_struct_packing(self) -> None:
        import asmpython  # noqa: F401 -- installs the builtins as a side effect

        for value in (0.0, 1.0, -1.0, 0.5, 3.14159, float("inf"), float("-inf")):
            expected, = struct.unpack("<q", struct.pack("<d", value))
            self.assertEqual(bitcast_f2i(value), expected)  # noqa: F821

    def test_bitcast_i2f_round_trips(self) -> None:
        import asmpython  # noqa: F401

        for value in (0.0, 1.0, -1.0, 0.5, 3.14159, 2.0 ** 100):
            bits = bitcast_f2i(value)  # noqa: F821
            self.assertEqual(bitcast_i2f(bits), value)  # noqa: F821

    def test_bitcast_i2f_of_zero_bits_is_positive_zero(self) -> None:
        import asmpython  # noqa: F401

        result = bitcast_i2f(0)  # noqa: F821
        self.assertEqual(result, 0.0)
        self.assertFalse(math.copysign(1.0, result) < 0)


class BitcastSemaTests(unittest.TestCase):
    def test_bitcast_f2i_requires_float_argument(self) -> None:
        module = _parse(
            "def f(x: int) -> int:\n"
            "    return bitcast_f2i(x)\n"
            "\n"
            "def main() -> int:\n"
            "    return f(1)\n"
        )
        with self.assertRaises(Exception):
            sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())
            ir_lower.lower_module(module)

    def test_bitcast_f2i_and_i2f_type_check_on_float_and_int(self) -> None:
        module = _parse(
            "def f(x: float, y: int) -> float:\n"
            "    bits = bitcast_f2i(x)\n"
            "    back = bitcast_i2f(bits + y)\n"
            "    return back\n"
        )
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())
        ir_lower.lower_module(module)


def _toolchain_available() -> bool:
    return sys.platform == "win32" and shutil.which("nasm") is not None


@unittest.skipUnless(
    _toolchain_available(), "requires Windows + nasm to build/load a real PE DLL"
)
class BitcastDllTests(unittest.TestCase):
    def test_bitcast_round_trips_a_real_c_double_via_ctypes(self) -> None:
        from asmpython._backends.x86_64 import __module_backend__ as backend
        from asmpython._backends.x86_64.pe_linker import link_pe
        from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path

        module = _parse(
            "def double_bits(x: float) -> int:\n"
            "    return bitcast_f2i(x)\n"
            "\n"
            "def bits_double(bits: int) -> float:\n"
            "    return bitcast_i2f(bits)\n"
        )
        for function in module.funcs:
            if function.name in ("double_bits", "bits_double"):
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
            exports=["double_bits", "bits_double"],
        )

        import tempfile
        import os

        fd, path_str = tempfile.mkstemp(suffix=".dll")
        dll_path = Path(path_str)
        with os.fdopen(fd, "wb") as stream:
            stream.write(dll_bytes)

        library = ctypes.WinDLL(str(dll_path))
        try:
            library.double_bits.restype = ctypes.c_int64
            library.double_bits.argtypes = [ctypes.c_double]
            library.bits_double.restype = ctypes.c_double
            library.bits_double.argtypes = [ctypes.c_int64]

            for value in (0.0, 1.0, -1.0, 0.5, 3.14159):
                expected_bits, = struct.unpack("<q", struct.pack("<d", value))
                bits = library.double_bits(ctypes.c_double(value))
                self.assertEqual(bits, expected_bits)
                back = library.bits_double(ctypes.c_int64(bits))
                self.assertEqual(back, value)
        finally:
            ctypes.windll.kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.FreeLibrary(library._handle)
            dll_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
