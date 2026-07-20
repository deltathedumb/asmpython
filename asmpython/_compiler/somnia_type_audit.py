"""Print semantic class signatures after analysis or a partial failure."""

from __future__ import annotations

import argparse
from pathlib import Path

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
}


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

    for owner in module.classes:
        if owner.name not in INTERESTING:
            continue
        signature = analyzer.classes.get(owner.name)
        print("CLASS", owner.name)
        print("  CLASS_VARS", [name for name, _annotation, _value in owner.class_vars])
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
                "DECORATORS",
                list(getattr(method_signature, "decorators", []) or []),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
