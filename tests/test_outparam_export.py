from __future__ import annotations

import ctypes
import shutil
import sys
import unittest
from pathlib import Path

from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze, SemaError
from asmpython._compiler import ir_lower


def _parse(source: str):
    return Parser(Lexer(source).tokenize(), frozenset()).parse()


class OutparamParserTests(unittest.TestCase):
    def test_outparam_annotation_preserves_pointee_kind(self) -> None:
        module = _parse(
            "def f(a: int, out: outparam[int]) -> int:\n"
            "    out[0] = 1\n"
            "    return 0\n"
        )
        function = module.funcs[0]
        self.assertEqual(function.param_types, [("int", None), ("outparam", "int")])

    def test_bare_outparam_defaults_to_int_pointee(self) -> None:
        module = _parse(
            "def f(out: outparam) -> int:\n"
            "    out[0] = 1\n"
            "    return 0\n"
        )
        self.assertEqual(module.funcs[0].param_types, [("outparam", "int")])


class OutparamSemaTests(unittest.TestCase):
    def test_outparam_write_on_exported_function_is_accepted(self) -> None:
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def get_size(runtime: int, value: int, out_size: outparam[int]) -> int:\n"
            "    out_size[0] = 42\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "get_size":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

    def test_outparam_on_non_exported_function_is_rejected(self) -> None:
        module = _parse(
            "def get_size(runtime: int, value: int, out_size: outparam[int]) -> int:\n"
            "    out_size[0] = 42\n"
            "    return 0\n"
            "\n"
            "def main() -> int:\n"
            "    return get_size(1, 2, 3)\n"
        )
        with self.assertRaises(SemaError):
            sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

    def test_outparam_write_index_must_be_an_int(self) -> None:
        # outparam[T] originally only accepted a literal-0 index (a single
        # scalar out-parameter). Later generalized to accept any int index
        # (a loop counter) too, matching inparam[T]'s read side, so that a
        # byte-buffer outparam[int8] (e.g. `uint8_t *buffer`, written
        # byte-by-byte -- see PortaPy's portapy_dict_key_copy_utf8) can be
        # written for real. Only a genuinely non-int index is still
        # rejected.
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def get_size(out_size: outparam[int]) -> int:\n"
            "    out_size['bad'] = 42\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "get_size":
                function.is_public_export = True
        with self.assertRaises(SemaError):
            sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

    def test_outparam_write_with_a_variable_index_is_accepted(self) -> None:
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def fill(out_size: outparam[int], i: int) -> int:\n"
            "    out_size[i] = 42\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "fill":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())

    def test_outparam_write_type_mismatch_is_rejected(self) -> None:
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def get_size(out_size: outparam[int]) -> int:\n"
            "    out_size[0] = 1.5\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "get_size":
                function.is_public_export = True
        with self.assertRaises(SemaError):
            sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())


class OutparamIrLowerTests(unittest.TestCase):
    def test_outparam_parameter_lowers_as_pointer_and_stores_directly(self) -> None:
        module = _parse(
            "from asmpython import Public, access\n"
            "\n"
            "@access(Public)\n"
            "def get_size(runtime: int, value: int, out_size: outparam[int]) -> int:\n"
            "    out_size[0] = 42\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "get_size":
                function.is_public_export = True
        sema_analyze(module, source_dir=None, collect_errors=False, active_extensions=frozenset())
        ir_module = ir_lower.lower_module(module)
        get_size_ir = next(f for f in ir_module.funcs if f.name == "get_size")
        self.assertEqual(get_size_ir.params[2].type.name, "ptr")
        store_ops = [
            instr for block in get_size_ir.blocks for instr in block.instrs
            if instr.op == "store"
        ]
        self.assertTrue(any(op.operands[0].type.name == "i64" for op in store_ops))


def _toolchain_available() -> bool:
    return sys.platform == "win32" and shutil.which("nasm") is not None


@unittest.skipUnless(
    _toolchain_available(), "requires Windows + nasm to build/load a real PE DLL"
)
class OutparamDllTests(unittest.TestCase):
    def test_outparam_writes_through_a_real_ctypes_byref_pointer(self) -> None:
        from asmpython._backends.x86_64 import __module_backend__ as backend
        from asmpython._backends.x86_64.pe_linker import link_pe
        from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path

        module = _parse(
            "def get_size(runtime: int, value: int, out_size: outparam[int]) -> int:\n"
            "    out_size[0] = 42\n"
            "    return 0\n"
        )
        for function in module.funcs:
            if function.name == "get_size":
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
            exports=["get_size"],
        )

        import tempfile
        import os

        fd, path_str = tempfile.mkstemp(suffix=".dll")
        dll_path = Path(path_str)
        with os.fdopen(fd, "wb") as stream:
            stream.write(dll_bytes)

        library = ctypes.WinDLL(str(dll_path))
        try:
            library.get_size.restype = ctypes.c_int64
            library.get_size.argtypes = [
                ctypes.c_int64, ctypes.c_int64, ctypes.POINTER(ctypes.c_int64)
            ]
            out_size = ctypes.c_int64(-1)
            status = library.get_size(1, 2, ctypes.byref(out_size))
            self.assertEqual(status, 0)
            self.assertEqual(out_size.value, 42)
        finally:
            ctypes.windll.kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            ctypes.windll.kernel32.FreeLibrary(library._handle)
            dll_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
