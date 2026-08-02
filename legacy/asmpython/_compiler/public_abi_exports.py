"""Automatic public-object ABI exports for native library builds.

ASMPython's generated functions already receive typed arguments in the target
platform ABI registers (SysV on Linux, Microsoft x64 on Windows). This pass
only needs to publish the NASM symbols for functions/classes the parser
already marked `is_public_export` (via `@access(Public)` / `@abi(...)` --
see ast_nodes.FuncDef/ClassDef and parser.py's `_eat_decorators`). Windows
library linking already uses `--export-all-symbols`; Linux shared libraries
export NASM `global` symbols.

Patches `Codegen.generate` like the other beta compatibility modules.
"""
from __future__ import annotations

from .codegen import Codegen


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


__all__ = ["_inject_public_exports", "_public_symbols"]
