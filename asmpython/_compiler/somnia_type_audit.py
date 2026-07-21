"""Print semantic class signatures after analysis or a partial failure."""

from __future__ import annotations

import argparse
import inspect
from dataclasses import fields, is_dataclass
from pathlib import Path

from . import ast_nodes as A
from .program import load_program
from .sema import SemaAnalyzer


INTERESTING = {
    "Property",
    "Vec3",
    "Transform",
    "SomniaObject",
    "ModelNode",
    "Camera",
    "MeshObject",
    "RenderService",
    "RenderFrame",
}

INTERESTING_METHODS = {
    "__get__",
    "transform",
    "target",
    "up",
    "color",
    "clear_color",
    "position",
    "rotation",
    "scale",
    "to_list",
    "to_dict",
    "type_name",
    "walk",
}


def _walk_ast(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return
    yield value
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _walk_ast(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_ast(key)
            yield from _walk_ast(item)
        return
    if is_dataclass(value) and not isinstance(value, type):
        for descriptor in fields(value):
            yield from _walk_ast(getattr(value, descriptor.name))


def _print_comprehension_source() -> None:
    print("ITER_ELEMENT_SOURCE")
    print(inspect.getsource(SemaAnalyzer._iter_element_type))
    print(
        "CHECK_EXPR_ACTIVE",
        SemaAnalyzer._check_expr.__module__,
        SemaAnalyzer._check_expr.__name__,
    )
    print(inspect.getsource(SemaAnalyzer._check_expr))
    source_lines = inspect.getsource(SemaAnalyzer._check_expr).splitlines()
    for index, line in enumerate(source_lines):
        if "Comprehension" not in line:
            continue
        start = max(0, index - 4)
        end = min(len(source_lines), index + 22)
        print("CHECK_EXPR_COMPREHENSION_SOURCE")
        print("\n".join(source_lines[start:end]))


def _expr_state(expression) -> tuple[str, str, object, object]:
    return (
        type(expression).__name__,
        A.expr_type(expression),
        getattr(expression, "inferred_type", None),
        getattr(expression, "list_el_type", None),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", type=Path)
    args = parser.parse_args(argv)

    entry = args.entry.resolve()
    module = load_program(entry.read_text(encoding="utf-8"), entry)
    _print_comprehension_source()

    original_iter_element_type = SemaAnalyzer._iter_element_type
    original_check_expr = SemaAnalyzer._check_expr
    original_bind_name = SemaAnalyzer._bind_name_from_value

    def audited_iter_element_type(self, expression, *extra, **keywords):
        result = original_iter_element_type(self, expression, *extra, **keywords)
        if isinstance(expression, A.MethodCall) and expression.method == "walk":
            print(
                "ITER_WALK",
                "RECEIVER",
                A.expr_type(expression.obj),
                "CALL",
                A.expr_type(expression),
                "CALL_EL",
                getattr(expression, "list_el_type", None),
                "RESULT",
                result,
            )
        return result

    def audited_bind_name(self, name, value, scope, *extra, **keywords):
        interesting = name == "current" or isinstance(value, A.BoolOp)
        if interesting:
            print(
                "BIND_BEFORE",
                name,
                "VALUE",
                _expr_state(value),
                "LEFT",
                _expr_state(value.left) if isinstance(value, A.BoolOp) else None,
                "RIGHT",
                _expr_state(value.right) if isinstance(value, A.BoolOp) else None,
                "SCOPE",
                scope.types.get(name),
            )
        result = original_bind_name(self, name, value, scope, *extra, **keywords)
        if interesting:
            print(
                "BIND_AFTER",
                name,
                "VALUE",
                _expr_state(value),
                "SCOPE",
                scope.types.get(name),
            )
        return result

    def audited_check_expr(self, expression, scope):
        if not (
            isinstance(expression, A.MethodCall)
            and expression.method == "startswith"
        ):
            return original_check_expr(self, expression, scope)
        print(
            "STARTSWITH_BEFORE",
            "OUTER",
            _expr_state(expression),
            "RECEIVER",
            _expr_state(expression.obj),
            "NAME",
            getattr(expression.obj, "name", None),
            "SCOPE",
            scope.types.get(getattr(expression.obj, "name", "")),
            "ACTIVE",
            original_check_expr.__module__,
            original_check_expr.__name__,
        )
        try:
            result = original_check_expr(self, expression, scope)
        except Exception as error:
            print(
                "STARTSWITH_ERROR",
                type(error).__name__ + ":",
                str(error),
                "OUTER",
                _expr_state(expression),
                "RECEIVER",
                _expr_state(expression.obj),
                "NAME",
                getattr(expression.obj, "name", None),
                "SCOPE",
                scope.types.get(getattr(expression.obj, "name", "")),
            )
            raise
        print(
            "STARTSWITH_AFTER",
            "OUTER",
            _expr_state(expression),
            "RECEIVER",
            _expr_state(expression.obj),
        )
        return result

    SemaAnalyzer._iter_element_type = audited_iter_element_type
    SemaAnalyzer._bind_name_from_value = audited_bind_name
    SemaAnalyzer._check_expr = audited_check_expr
    analyzer = SemaAnalyzer(module, source_dir=entry.parent, collect_errors=True)
    try:
        analyzer.analyze()
        print("ANALYZE PASS")
    except Exception as error:
        print("ANALYZE ERROR", type(error).__name__ + ":", str(error))

    for node in _walk_ast(module):
        if isinstance(node, A.BoolOp):
            print(
                "BOOLOP",
                node.op,
                "VALUE",
                _expr_state(node),
                "LEFT",
                _expr_state(node.left),
                "RIGHT",
                _expr_state(node.right),
                "POS",
                node.pos,
            )
        if isinstance(node, (A.Comprehension, A.DictComprehension)):
            iterator = node.iter
            print(
                "COMPREHENSION",
                type(iterator).__name__,
                "METHOD",
                getattr(iterator, "method", None),
                "TYPE",
                A.expr_type(iterator),
                "EL",
                getattr(iterator, "list_el_type", None),
                "VARS",
                vars(node),
            )

    for owner in module.classes:
        if owner.name not in INTERESTING:
            continue
        signature = analyzer.classes.get(owner.name)
        print("CLASS", owner.name)
        print("  CLASS_VARS", [name for name, _annotation, _value in owner.class_vars])
        for method in owner.methods:
            if method.name in INTERESTING_METHODS:
                print("  AST_METHOD", method.name, "RET", method.ret_type)
        if signature is None:
            print("  SIGNATURE <missing>")
            continue
        print("  FIELDS", dict(signature.fields))
        print("  FIELD_ELEMENTS", dict(signature.field_el_types))
        for method_name, method_signature in signature.methods.items():
            if method_name not in INTERESTING_METHODS:
                continue
            print(
                "  METHOD",
                method_name,
                "RET",
                method_signature.ret_type,
                "RET_EL",
                getattr(method_signature, "ret_el_type", None),
                "DECORATORS",
                list(getattr(method_signature, "decorators", []) or []),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
