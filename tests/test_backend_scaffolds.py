from __future__ import annotations

import importlib
import re

import pytest

from asmpython._backends import get_backend, registered_aliases, registered_names
from asmpython._backends._scaffold import ScaffoldBackend
from asmpython._backends.scaffolds import SCAFFOLD_BACKENDS, SCAFFOLD_BACKEND_SPECS


BACKEND_MODULES = {
    "x86": "x86",
    "arm": "arm",
    "thumb": "thumb",
    "riscv": "riscv",
    "mips": "mips",
    "powerpc": "powerpc",
    "avr": "avr",
    "8051": "mcs51",
    "pic": "pic",
    "xtensa": "xtensa",
    "6502": "mos6502",
    "z80": "z80",
    "jvm": "jvm",
    "android": "android",
    "python-bytecode": "python_bytecode",
    "webassembly": "webassembly",
    "beam": "beam",
    "spirv": "spirv",
    "ebpf": "ebpf",
    "cuda": "cuda",
    "amdgpu": "amdgpu",
    "glsl": "glsl",
    "hlsl": "hlsl",
    "wgsl": "wgsl",
    "metal": "metal",
    "verilog": "verilog",
    "systemverilog": "systemverilog",
    "vhdl": "vhdl",
}

EXPECTED_BACKENDS = set(BACKEND_MODULES)


def test_every_planned_backend_is_registered_once() -> None:
    names = [spec.name for spec in SCAFFOLD_BACKEND_SPECS]
    assert len(names) == len(set(names))
    assert set(names) == EXPECTED_BACKENDS
    assert EXPECTED_BACKENDS.issubset(set(registered_names()))
    assert set(SCAFFOLD_BACKENDS) == EXPECTED_BACKENDS


@pytest.mark.parametrize("name", sorted(EXPECTED_BACKENDS))
def test_scaffold_operations_fail_loudly(name: str) -> None:
    backend = get_backend(name)
    assert isinstance(backend, ScaffoldBackend)
    assert backend.is_scaffold is True
    assert backend.implemented is False
    assert backend.default_linker == "none"
    assert backend.requested_args == []
    assert backend.planned_parameters

    expected = re.escape(name)
    with pytest.raises(NotImplementedError, match=expected):
        backend.compile(object(), {})  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match=expected):
        backend.link([], {})
    with pytest.raises(NotImplementedError, match=expected):
        backend.validate_ir(object())  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match=expected):
        backend.emit_object(object())  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match=expected):
        backend.emit_source(object())  # type: ignore[arg-type]
    with pytest.raises(NotImplementedError, match=expected):
        backend.package({})


@pytest.mark.parametrize("name,module_name", sorted(BACKEND_MODULES.items()))
def test_backend_package_exposes_registered_scaffold(name: str, module_name: str) -> None:
    module = importlib.import_module(f"asmpython._backends.{module_name}")
    registered = get_backend(name)
    assert module.__module_backend__ is registered
    assert module.backend is registered


def test_convenience_aliases_resolve_to_canonical_scaffolds() -> None:
    expected_aliases = {
        "ARM": "arm",
        "RISC-V": "riscv",
        "ppc": "powerpc",
        "jar": "jvm",
        "pyc": "python-bytecode",
        "wasm": "webassembly",
        "SPIR-V": "spirv",
        "bpf": "ebpf",
        "system-verilog": "systemverilog",
    }
    aliases = registered_aliases()
    for alias, canonical in expected_aliases.items():
        assert aliases[alias] == canonical
        assert get_backend(alias) is get_backend(canonical)


def test_production_special_case_names_are_not_shadowed() -> None:
    assert "x86-64" not in EXPECTED_BACKENDS
    assert "legacy" not in EXPECTED_BACKENDS
    assert "ternary" not in EXPECTED_BACKENDS
