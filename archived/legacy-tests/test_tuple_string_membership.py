from __future__ import annotations

import ctypes
import os
from pathlib import Path
import tempfile

import pytest

from asmpython._backends.x86_64 import __module_backend__ as backend
from asmpython._backends.x86_64.pe_linker import link_pe
from asmpython._compiler.ssa import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze
from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path


SOURCE = """\
from asmpython import Public, access

@access(Public)
def check() -> int:
    text = "x+=y"
    two = text[1:3]
    if two != "+=":
        return 10
    if two in ("==", "!=", "<=", ">=", "+=", "-=", "*=", "/="):
        return 20
    return 30
"""


def _lower_source():
    module = Parser(Lexer(SOURCE).tokenize(), frozenset()).parse()
    sema_analyze(
        module,
        source_dir=None,
        collect_errors=False,
        active_extensions=frozenset(),
    )
    return ir_lower.lower_module(module)


def test_tuple_literal_string_membership_uses_value_comparison() -> None:
    function = next(fn for fn in _lower_source().funcs if fn.name == "check")
    string_equal_calls = [
        instruction
        for block in function.blocks
        for instruction in block.instrs
        if instruction.op == "call"
        and instruction.operands
        and instruction.operands[0] == "_abi_str_eq"
    ]
    assert len(string_equal_calls) == 2


@pytest.mark.skipif(os.name != "nt", reason="requires a loadable Windows DLL")
def test_sliced_string_matches_tuple_literal_member_in_real_dll() -> None:
    program = next(
        iter(
            backend.compile(
                _lower_source(), {"target_os": "windows", "abi": "win64"}
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
        path = Path(temporary) / "tuple-string-membership.dll"
        path.write_bytes(library_bytes)
        library = ctypes.CDLL(str(path))
        try:
            library.check.argtypes = []
            library.check.restype = ctypes.c_int64
            assert library.check() == 20
        finally:
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(library._handle))
