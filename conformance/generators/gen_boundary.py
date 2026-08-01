"""Generate the boundary cross-product: every VALUE KIND through every TRIP.

    python conformance/generators/gen_boundary.py
    python conformance/regen.py --filter generated/boundary

A value's identity must survive being moved. That is close to the whole content
of a dynamic language's data model, and it is where a compiled implementation
is most likely to diverge: representation tends to follow the DECLARED type of
wherever a value is stored, so reading it back through a weaker type reads a
different representation than was written.

The cross-product holds the value fixed and varies only the trip, so a failure
localizes itself. The path IS the coordinate:

    cases/generated/boundary/<trip>/<kind>.py

If `container_roundtrip/str` fails while `container_roundtrip/int` passes, the
defect is (container_roundtrip x str) and nothing else needs to be read. That
is the property that makes a generated suite triagable: thousands of failures
collapse into "which axis coordinates fail", not "here are thousands of
programs".

Every case prints the value AND an equality check against the original, because
those fail differently: printing exercises the formatter, equality exercises the
comparison path, and an implementation can get one right and the other wrong.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cases" / "generated" / "boundary"

#: A literal per kind, plus a name for the path. Chosen so every kind has an
#: unambiguous repr and no ambient state: no sets of strings (PYTHONHASHSEED
#: makes their order per-process), no id(), no clocks.
KINDS: dict[str, str] = {
    "str": "'abc'",
    "str-empty": "''",
    "int": "42",
    "int-zero": "0",
    "int-negative": "-7",
    "int-big": "9223372036854775808",
    "float": "3.5",
    "float-zero": "0.0",
    "bool-true": "True",
    "bool-false": "False",
    "none": "None",
    "list": "[1, 2]",
    "list-nested": "[[1], [2]]",
    "dict": "{'k': 1}",
    "tuple": "(1, 'two')",
    "tuple-empty": "()",
}

#: Each trip takes the literal in and must hand the SAME value back. The body
#: defines `move(v)`; the harness template does the printing and comparing.
TRIPS: dict[str, str] = {
    "identity": "def move(v):\n    return v\n",

    "unannotated-param": (
        "def _through(x):\n    return x\n\n"
        "def move(v):\n    return _through(v)\n"
    ),

    "object-annotated-param": (
        "def _through(x: object):\n    return x\n\n"
        "def move(v):\n    return _through(v)\n"
    ),

    "two-calls": (
        "def _inner(x):\n    return x\n\n"
        "def _outer(x):\n    return _inner(x)\n\n"
        "def move(v):\n    return _outer(v)\n"
    ),

    "list-roundtrip": "def move(v):\n    box = [v]\n    return box[0]\n",

    "list-append-roundtrip": (
        "def move(v):\n    box = []\n    box.append(v)\n    return box[0]\n"
    ),

    "nested-list-roundtrip": "def move(v):\n    box = [[v]]\n    return box[0][0]\n",

    "tuple-roundtrip": "def move(v):\n    box = (v,)\n    return box[0]\n",

    "dict-value-roundtrip": "def move(v):\n    d = {'k': v}\n    return d['k']\n",

    "instance-field-roundtrip": (
        "class _Box:\n    def __init__(self, v):\n        self.v = v\n\n"
        "def move(v):\n    return _Box(v).v\n"
    ),

    "comprehension": "def move(v):\n    return [x for x in [v]][0]\n",

    "for-loop": (
        "def move(v):\n    out = v\n    for x in [v]:\n        out = x\n    return out\n"
    ),

    "returned-from-closure": (
        "def _make(x):\n    def get():\n        return x\n    return get\n\n"
        "def move(v):\n    return _make(v)()\n"
    ),

    "default-argument": (
        "def move(v):\n    def _inner(x=None):\n        return x\n    return _inner(v)\n"
    ),

    "star-args": (
        "def _through(*a):\n    return a[0]\n\n"
        "def move(v):\n    return _through(v)\n"
    ),
}

TEMPLATE = """# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
{body}
_original = {literal}
_moved = move(_original)
print(_moved)
print(_moved == _original)
print(type(_moved).__name__)
"""


def main() -> int:
    written = 0
    for trip_name, body in TRIPS.items():
        for kind_name, literal in KINDS.items():
            path = OUT / trip_name / f"{kind_name}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                TEMPLATE.format(body=body, literal=literal), encoding="utf-8"
            )
            written += 1
    print(f"gen_boundary: {written} cases "
          f"({len(TRIPS)} trips x {len(KINDS)} kinds) -> {OUT}")
    print("now run: python conformance/regen.py --filter generated/boundary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
