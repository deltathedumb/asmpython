"""Print post-construction semantic class signatures for a project entry."""

from __future__ import annotations

import argparse
from pathlib import Path

from .program import load_program
from .sema import SemaAnalyzer


INTERESTING = {
    "SomniaObject",
    "ModelNode",
    "Camera",
    "MeshObject",
    "RenderService",
    "Transform",
    "Vec3",
    "RenderFrame",
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", type=Path)
    args = parser.parse_args(argv)

    entry = args.entry.resolve()
    module = load_program(entry.read_text(encoding="utf-8"), entry)
    analyzer = SemaAnalyzer(module, source_dir=entry.parent, collect_errors=True)

    for owner in module.classes:
        if owner.name not in INTERESTING:
            continue
        signature = analyzer.classes.get(owner.name)
        print("CLASS", owner.name)
        print("  CLASS_VARS", [name for name, _annotation, _value in owner.class_vars])
        if signature is not None:
            print("  FIELDS", dict(signature.fields))
            print("  FIELD_ELEMENTS", dict(signature.field_el_types))
            for method_name, method_signature in signature.methods.items():
                if method_name in {
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
                }:
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
