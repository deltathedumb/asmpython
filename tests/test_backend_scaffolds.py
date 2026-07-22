from __future__ import annotations

import re

import pytest

from asmpython._backends import get_backend, registered_aliases, registered_names
from asmpython._backends._scaffold import ScaffoldBackend
from asmpython._backends.scaffolds import SCAFFOLD_BACKENDS, SCAFFOLD_BACKEND_SPECS


EXPECTED_BACKENDS = {
    "x86",
    "arm",
    "thumb",
    "riscv",
    "mips",
    "powerpc",
    "avr",
    "8051",
    "pic",
    "xtensa",
    "6502",
    "z80",
    "jvm",
    "python-bytecode",
    "webassembly",
    "beam",
    "spirv",
    "ebpf",
    "cuda",
    "amdgpu",
    "glsl",
    "hlsl",
    "wgsl",
    "metal",
    "verilog",
    "systemverilog",
    "vhdl",
}


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
