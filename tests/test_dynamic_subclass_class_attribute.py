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
from asmpython._compiler.sema import analyze
from asmpython._runtime.build import build_abi_shims, build_runtime, runtime_object_path


SOURCE = """
from asmpython import Public, access

class Base:
    value = 1

class Child(Base):
    pass

@access(Public)
def check() -> int:
    Child.value = 42
    return Child().value
"""


def _lower():
    module = Parser(Lexer(SOURCE).tokenize(), frozenset()).parse()
    analyze(
        module,
        source_dir=None,
        collect_errors=False,
        active_extensions=frozenset(),
    )
    module.force_module_init = True
    return ir_lower.lower_module(module)


def test_dynamic_subclass_class_attribute_lowers() -> None:
    lowered = _lower()
    assert any(function.name == "check" for function in lowered.funcs)


@pytest.mark.skipif(os.name != "nt", reason="requires a loadable Windows DLL")
def test_dynamic_subclass_class_attribute_executes() -> None:
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
        path = Path(temporary) / "dynamic-class-attribute.dll"
        path.write_bytes(library_bytes)
        library = ctypes.CDLL(str(path))
        try:
            library.check.argtypes = []
            library.check.restype = ctypes.c_int64
            assert library.check() == 42
        finally:
            ctypes.windll.kernel32.FreeLibrary(ctypes.c_void_p(library._handle))
