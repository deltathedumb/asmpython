from __future__ import annotations

import ctypes
import shutil
import sys
import unittest
from pathlib import Path

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze, SemaError
from asmpython._compiler.ssa import ir_lower


def _parse(source: str):
    return Parser(Lexer(source).tokenize(), frozenset()).parse()


class InparamParserTests(unittest.TestCase):
    def test_inparam_annotation_preserves_element_kind(self) -> None:
        module = _parse(
            "def f(items: inparam[int], count: int) -> int:\n"
            "    return items[0]\n"
        )
        self.assertEqual(
            module.funcs[0].param_types, [("inparam", "int"), ("int", None)]
        )


class InparamSemaTests(unittest.TestCase):
    def test_inparam_read_on_exported_function_is_accepted(self) -> None:
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def first(items: inparam[int], count: int) -> int:\n"
            "    return items[0]\n"
        )
        for function in module.funcs:
            if function.name == "first":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

    def test_inparam_on_non_exported_helper_function_is_accepted(self) -> None:
        # See outparam's matching test for the full rationale: a
        # non-exported internal helper called from an exported function,
        # passing its inparam/outparam pointer straight through, is a
        # legitimate and common pattern (PortaPy's dict_glue.c port needed
        # exactly this for its shared ASCII-key-decoding helper).
        module = _parse(
            "def first(items: inparam[int], count: int) -> int:\n"
            "    return items[0]\n"
            "\n"
            "def main() -> int:\n"
            "    return 0\n"
        )
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

    def test_inparam_read_allows_a_variable_index(self) -> None:
        # Unlike outparam (exactly one pointee, literal 0 only), inparam is
        # a real array -- a loop counter or other computed index is valid.
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def get_at(items: inparam[int], i: int) -> int:\n"
            "    return items[i]\n"
        )
        for function in module.funcs:
            if function.name == "get_at":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())


class InparamIrLowerTests(unittest.TestCase):
    def test_inparam_parameter_lowers_as_pointer_with_index_arithmetic(self) -> None:
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def first(items: inparam[int], count: int) -> int:\n"
            "    return items[0]\n"
        )
        for function in module.funcs:
            if function.name == "first":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())
        ir_module = ir_lower.lower_module(module)
        first_ir = next(f for f in ir_module.funcs if f.name == "first")
        self.assertEqual(first_ir.params[0].type.name, "ptr")
        ops = [instr.op for block in first_ir.blocks for instr in block.instrs]
        self.assertIn("gep", ops)
        self.assertIn("load", ops)


def _toolchain_available() -> bool:
    return sys.platform == "win32" and shutil.which("nasm") is not None


@unittest.skipUnless(
    _toolchain_available(), "requires Windows + nasm to build/load a real PE DLL"
)
class InparamDllTests(unittest.TestCase):
    def test_inparam_and_outparam_together_via_real_ctypes_array(self) -> None:
        from asmpython._backends.x86_64 import __module_backend__ as backend
        from asmpython._backends.x86_64.pe_linker import link_pe
        from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path

        module = _parse(
            "def sum_items(items: inparam[int], count: int, out_sum: outparam[int]) -> int:\n"
            "    total = 0\n"
            "    i = 0\n"
            "    while i < count:\n"
            "        total = total + items[i]\n"
            "        i = i + 1\n"
            "    out_sum[0] = total\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "sum_items":
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
            exports=["sum_items"],
        )

        import tempfile
        import os

        fd, path_str = tempfile.mkstemp(suffix=".dll")
        dll_path = Path(path_str)
        with os.fdopen(fd, "wb") as stream:
            stream.write(dll_bytes)

        library = ctypes.WinDLL(str(dll_path))
        try:
            library.sum_items.restype = ctypes.c_int64
            library.sum_items.argtypes = [
                ctypes.POINTER(ctypes.c_int64), ctypes.c_int64, ctypes.POINTER(ctypes.c_int64)
            ]
            items = (ctypes.c_int64 * 5)(10, 20, 3, 4, 5)
            out_sum = ctypes.c_int64(-1)
            status = library.sum_items(items, 5, ctypes.byref(out_sum))
            self.assertEqual(status, 0)
            self.assertEqual(out_sum.value, 42)
        finally:
            ctypes.windll.kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.FreeLibrary(library._handle)
            dll_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
