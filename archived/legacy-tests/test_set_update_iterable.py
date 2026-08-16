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
    names: set = set()
    values: list = ["value", "other"]
    names.update(values)
    if "value" not in names:
        return 10
    if "other" not in names:
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


def test_set_update_list_lowers_to_per_member_insert() -> None:
    function = next(fn for fn in _lower_source().funcs if fn.name == "check")
    calls = [
        instruction.operands[0]
        for block in function.blocks
        for instruction in block.instrs
        if instruction.op == "call" and instruction.operands
    ]
    assert "_abi_dict_set" in calls
    assert "_abi_dict_update" not in calls


@pytest.mark.skipif(os.name != "nt", reason="requires a loadable Windows DLL")
def test_set_update_list_executes_in_real_dll() -> None:
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
        path = Path(temporary) / "set-update-list.dll"
        path.write_bytes(library_bytes)
        library = ctypes.CDLL(str(path))
        try:
            library.check.argtypes = []
            library.check.restype = ctypes.c_int64
            assert library.check() == 30
        finally:
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(library._handle))
