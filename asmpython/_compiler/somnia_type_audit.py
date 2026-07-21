"""Print semantic class signatures after analysis or a partial failure."""

from __future__ import annotations

import argparse
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", type=Path)
    args = parser.parse_args(argv)

    entry = args.entry.resolve()
    module = load_program(entry.read_text(encoding="utf-8"), entry)

    original_iter_element_type = SemaAnalyzer._iter_element_type

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

    SemaAnalyzer._iter_element_type = audited_iter_element_type
    analyzer = SemaAnalyzer(module, source_dir=entry.parent, collect_errors=True)
    try:
        analyzer.analyze()
        print("ANALYZE PASS")
    except Exception as error:
        print("ANALYZE ERROR", type(error).__name__ + ":", str(error))

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
