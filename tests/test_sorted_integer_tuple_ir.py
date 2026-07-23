from __future__ import annotations

import ctypes
import os
from pathlib import Path
import tempfile

import pytest

from asmpython._backends.x86_64 import __module_backend__ as backend
from asmpython._backends.x86_64.pe_linker import link_pe
from asmpython._compiler import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze
from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path


SOURCE = """
from asmpython import Public, access

@access(Public)
def check() -> int:
    items: list[tuple[int, int]] = [(7, 1), (2, 3), (5, 4)]
    ordered = sorted(items)
    first, ignored = ordered[0]
    return first
"""


def _lower():
    module = Parser(Lexer(SOURCE).tokenize(), frozenset()).parse()
    analyze(
        module,
        source_dir=None,
        collect_errors=False,
        active_extensions=frozenset(),
    )
    return ir_lower.lower_module(module)


def test_sorted_integer_tuple_list_uses_integer_pair_sort() -> None:
    calls = {
        instruction.operands[0]
        for function in _lower().funcs
        for block in function.blocks
        for instruction in block.instrs
        if instruction.op == "call" and instruction.operands
    }
    assert "_abi_sort_pairs_int" in calls
    assert "_abi_sort_items" not in calls


@pytest.mark.skipif(os.name != "nt", reason="requires a loadable Windows DLL")
def test_sorted_integer_tuple_list_executes() -> None:
    program = next(
        iter(
            backend.compile(
                _lower(), {"target_os": "windows", "abi": "win64"}
            ).values()
        )
    )
    build_runtime("windows")
    library_bytes = link_pe(
        [
            program,
            build_abi_shims("windows").read_bytes(),
            runtime_object_path("windows").read_bytes(),
        ],
        is_library=True,
        exports=["check"],
    )
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "sorted-int-tuple.dll"
        path.write_bytes(library_bytes)
        library = ctypes.CDLL(str(path))
        try:
            library.check.argtypes = []
            library.check.restype = ctypes.c_int64
            assert library.check() == 2
        finally:
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(library._handle))
