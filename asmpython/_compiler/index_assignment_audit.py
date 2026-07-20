"""Print post-sema types for every indexed assignment in a project."""

from __future__ import annotations

import argparse
from pathlib import Path

from . import ast_nodes as A
from .object_flow_compat_fixes import _walk_statements
from .program import load_program
from .sema import SemaAnalyzer


def _expr_summary(expression) -> str:
    fields = [
        "node=" + type(expression).__name__,
        "type=" + A.expr_type(expression),
    ]
    for name in (
        "name",
        "inferred_type",
        "list_el_type",
        "value_type",
    ):
        if hasattr(expression, name):
            fields.append(name + "=" + repr(getattr(expression, name)))
    if isinstance(expression, A.Attr):
        fields.append("attr=" + expression.name)
        fields.append("receiver=" + _expr_summary(expression.obj))
    if isinstance(expression, A.Subscript):
        fields.append("object=" + _expr_summary(expression.obj))
        fields.append("index=" + _expr_summary(expression.index))
    return " ".join(fields)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", type=Path)
    args = parser.parse_args(argv)

    entry = args.entry.resolve()
    module = load_program(entry.read_text(encoding="utf-8"), entry)
    analyzer = SemaAnalyzer(module, source_dir=entry.parent, collect_errors=True)
    try:
        analyzer.analyze()
        print("ANALYZE PASS")
    except Exception as error:
        print("ANALYZE ERROR", type(error).__name__ + ":", str(error))

    count = 0
    definitions = [("<module>", module.body)]
    definitions.extend((function.name, function.body) for function in module.funcs)
    for owner in module.classes:
        definitions.extend(
            (owner.name + "." + method.name, method.body)
            for method in owner.methods
        )

    for definition_name, body in definitions:
        for statement in _walk_statements(body):
            if not isinstance(statement, A.IndexAssign):
                continue
            count += 1
            print(
                "INDEX_ASSIGN",
                definition_name,
                "POS",
                str(statement.pos),
            )
            print("  TARGET", _expr_summary(statement.target))
            print("  OBJECT", _expr_summary(statement.target.obj))
            print("  INDEX", _expr_summary(statement.target.index))
            print("  VALUE", _expr_summary(statement.value))
    print("COUNT", count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
