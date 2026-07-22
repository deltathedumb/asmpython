from __future__ import annotations

import asmpython._compiler  # activates compatibility patches
from asmpython._compiler import ast_nodes as A
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.public_abi_compat_fixes import _inject_public_exports


def _parse(source: str) -> A.Module:
    return Parser(Lexer(source).tokenize()).parse()


def test_parser_preserves_public_access_and_explicit_abi() -> None:
    module = _parse(
        '''from asmpython import Public, access, C, abi

@access(Public)
@abi(C)
def add(left: int, right: int) -> int:
    return left + right
'''
    )
    function = next(function for function in module.funcs if function.name == "add")
    assert function.access_policy == "Public"
    assert function.abi_name == "C"
    assert function.is_public_export is True


def test_private_access_does_not_auto_export() -> None:
    module = _parse(
        '''from asmpython import Class, access

@access(Class)
def helper(value: int) -> int:
    return value
'''
    )
    function = next(function for function in module.funcs if function.name == "helper")
    assert function.access_policy == "Class"
    assert function.abi_name == "AutoABI"
    assert function.is_public_export is False


def test_explicit_abi_exports_without_public_access() -> None:
    module = _parse(
        '''from asmpython import C, abi

@abi(C)
def callback(value: int) -> int:
    return value
'''
    )
    function = next(function for function in module.funcs if function.name == "callback")
    assert function.is_public_export is True
    assert function.abi_name == "C"


class _FakeCodegen:
    def __init__(self, module: A.Module) -> None:
        self.mod = module
        self.class_ids = {"Widget": 7}
        self.class_var_labels = {"Widget.answer": "__cv_Widget__answer"}

    def _user_symbol(self, name: str) -> str:
        return name

    def _method_symbol(self, class_name: str, method_name: str) -> str:
        return f"{class_name}__{method_name}"


def test_codegen_publishes_public_functions_and_class_objects() -> None:
    api = A.FuncDef(name="api", params=[], body=[])
    api.is_public_export = True
    api.abi_name = "AutoABI"
    method = A.FuncDef(name="read", params=["self"], body=[])
    widget = A.ClassDef(
        name="Widget",
        parent=None,
        methods=[method],
        class_vars=[("answer", ("int", None), A.IntLit(42))],
    )
    widget.is_public_export = True
    module = A.Module(funcs=[api], body=[], classes=[widget])

    assembly = _inject_public_exports(
        "BITS 64\ndefault rel\nsection .text\napi:\n    ret\n",
        _FakeCodegen(module),
    )

    assert "global api" in assembly
    assert "global Widget__read" in assembly
    assert "global __cv_Widget__answer" in assembly
    assert "global Widget" in assembly
    assert "Widget: dq 7" in assembly
    assert "asmpython-export symbol=api abi=auto" in assembly
