"""Automatic public-object ABI exports for native library builds.

ASMPython's generated functions already receive typed arguments in the target
platform ABI registers (SysV on Linux, Microsoft x64 on Windows).  This pass
therefore only needs to preserve ``@access(Public)`` / ``@abi(...)`` metadata
and publish the corresponding NASM symbols. Windows library linking already
uses ``--export-all-symbols``; Linux shared libraries export NASM ``global``
symbols.

The patch is deliberately isolated like the other beta compatibility modules.
"""
from __future__ import annotations

from typing import Any

from . import ast_nodes as A
from .codegen import Codegen
from .parser import Parser


_ACCESS_PRESETS = {
    "Public",
    "Module",
    "Package",
    "Subclass",
    "Class",
    "Instance",
    "NoAccess",
}


def _decorator_metadata(tokens: list[Any]) -> tuple[str | None, str | None]:
    """Return ``(access_policy, abi_name)`` from consumed decorator tokens."""
    access_policy: str | None = None
    abi_name: str | None = None
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.kind != "OP" or token.value != "@":
            index += 1
            continue
        index += 1
        if index >= len(tokens) or tokens[index].kind != "NAME":
            continue
        name = tokens[index].value
        index += 1
        # Preserve a dotted decorator identity but only the leaf function name
        # matters for asmpython.access / asmpython.abi.
        while (
            index + 1 < len(tokens)
            and tokens[index].kind == "OP"
            and tokens[index].value == "."
            and tokens[index + 1].kind == "NAME"
        ):
            name = tokens[index + 1].value
            index += 2
        if index >= len(tokens) or tokens[index].kind != "OP" or tokens[index].value != "(":
            continue
        depth = 0
        first_value: str | None = None
        while index < len(tokens):
            current = tokens[index]
            if current.kind == "OP" and current.value in ("(", "[", "{"):
                depth += 1
            elif current.kind == "OP" and current.value in (")", "]", "}"):
                depth -= 1
                if depth == 0:
                    index += 1
                    break
            elif depth == 1 and first_value is None:
                if current.kind == "NAME":
                    first_value = str(current.value)
                elif current.kind == "STRING":
                    first_value = str(current.value)
            index += 1
        if name == "access" and first_value in _ACCESS_PRESETS:
            access_policy = first_value
        elif name == "abi" and first_value:
            abi_name = first_value
    return access_policy, abi_name


def _set_export_metadata(node: object, metadata: tuple[str | None, str | None]) -> None:
    policy, abi_name = metadata
    setattr(node, "access_policy", policy)
    setattr(node, "abi_name", abi_name or "AutoABI")
    setattr(node, "is_public_export", policy == "Public" or abi_name is not None)


_original_eat_decorators = Parser._eat_decorators
_original_parse_funcdef = Parser._parse_funcdef
_original_parse_classdef = Parser._parse_classdef


def _eat_decorators(self: Parser) -> list[str]:
    start = self.i
    names = _original_eat_decorators(self)
    consumed = list(self.toks[start:self.i])
    self._asmpython_export_metadata = _decorator_metadata(consumed)
    return names


def _parse_funcdef(self: Parser, decorators: list[str] | None = None) -> A.FuncDef:
    metadata = getattr(self, "_asmpython_export_metadata", (None, None))
    self._asmpython_export_metadata = (None, None)
    node = _original_parse_funcdef(self, decorators=decorators)
    _set_export_metadata(node, metadata)
    return node


def _parse_classdef(self: Parser, decorators: list[str] | None = None) -> A.ClassDef:
    # Method decorators are consumed while the original class parser runs, so
    # retain the class-level declaration separately.
    metadata = getattr(self, "_asmpython_export_metadata", (None, None))
    self._asmpython_export_metadata = (None, None)
    node = _original_parse_classdef(self, decorators=decorators)
    _set_export_metadata(node, metadata)
    return node


Parser._eat_decorators = _eat_decorators
Parser._parse_funcdef = _parse_funcdef
Parser._parse_classdef = _parse_classdef


def _public_symbols(codegen: Codegen) -> tuple[list[str], list[tuple[str, int]]]:
    symbols: list[str] = []
    class_markers: list[tuple[str, int]] = []

    def add(symbol: str) -> None:
        if symbol and symbol not in symbols:
            symbols.append(symbol)

    for function in codegen.mod.funcs:
        if getattr(function, "is_public_export", False):
            add(function.asm_symbol or codegen._user_symbol(function.name))

    for class_ in codegen.mod.classes:
        class_public = bool(getattr(class_, "is_public_export", False))
        for method in class_.methods:
            if class_public or getattr(method, "is_public_export", False):
                add(codegen._method_symbol(class_.name, method.name))
        if class_public:
            class_id = int(getattr(codegen, "class_ids", {}).get(class_.name, -1))
            class_markers.append((class_.name, class_id))
            for field_name, _annotation, _value in getattr(class_, "class_vars", ()):
                label = getattr(codegen, "class_var_labels", {}).get(
                    f"{class_.name}.{field_name}"
                )
                if label:
                    add(label)
    return symbols, class_markers


def _inject_public_exports(assembly: str, codegen: Codegen) -> str:
    symbols, class_markers = _public_symbols(codegen)
    if not symbols and not class_markers:
        return assembly

    lines = assembly.splitlines()
    insert_at = 0
    for index, line in enumerate(lines):
        if line.strip() == "default rel":
            insert_at = index + 1
            break
    declarations = [f"global {symbol}" for symbol in symbols]
    for class_name, _class_id in class_markers:
        declarations.append(f"global {class_name}")
    lines[insert_at:insert_at] = declarations

    if class_markers:
        lines.append("section .rodata")
        for class_name, class_id in class_markers:
            lines.append(f"{class_name}: dq {class_id}")

    manifest = []
    for symbol in symbols:
        manifest.append(f"; asmpython-export symbol={symbol} abi=auto")
    for class_name, class_id in class_markers:
        manifest.append(
            f"; asmpython-export symbol={class_name} kind=class id={class_id} abi=auto"
        )
    lines[insert_at + len(declarations):insert_at + len(declarations)] = manifest
    return "\n".join(lines) + "\n"


_original_generate = Codegen.generate


def _generate(self: Codegen) -> str:
    return _inject_public_exports(_original_generate(self), self)


Codegen.generate = _generate


__all__ = ["_decorator_metadata", "_inject_public_exports", "_public_symbols"]
