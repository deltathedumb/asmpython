"""Serpent's standard-library bindings.

Each submodule here defines a `BINDINGS` dict:

    BINDINGS = {
        "name_in_serpent": Func(arg_types=(...), ret_type="...", c_name="..."),
        "constant_name": Const(ty="float", value=3.14),
    }

The compiler reads these at import-resolution time and uses them to:
  - emit `extern <c_name>` declarations in the .asm
  - dispatch calls with the correct argument types into the C ABI
  - intern constants in .rodata and substitute them at use sites
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Func:
    """Foreign function: maps a serpent name to a C ABI symbol with typed args."""
    arg_types: tuple[str, ...]   # each "int" | "float" | "str"
    ret_type: str                # "int" | "float" | "str"
    c_name: str


@dataclass(frozen=True)
class Const:
    ty: str
    value: object
