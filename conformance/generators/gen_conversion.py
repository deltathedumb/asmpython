"""Generate the conversion cross-product: every SOURCE through every CONSTRUCTOR.

    python conformance/generators/gen_conversion.py
    python conformance/regen.py --groups generated/conversion

The fourth product. boundary moves a value, consumer reads a container,
operator combines a pair; this one CONVERTS one container kind into another.

    cases/generated/conversion/<constructor>/<source>.py

Conversion is where a container's iteration order, its element representation
and the target's own invariants all have to agree at once. `dict(pairs)`
requires each element to unpack to exactly two; `set(...)` requires elements to
be hashable and drops duplicates; `list(dict)` yields KEYS, not items, which is
the single most commonly mis-implemented conversion in the language; `sorted()`
imposes an ordering the source never had.

Every cell is emitted, including the ones CPython rejects -- the body catches
TypeError and ValueError and prints the name, so `dict("ab")` is a recordable
case whose expected output is ValueError. Same reasoning as gen_operator: an
implementation that quietly produces something where CPython raises is worse
than one that produces the wrong value, and only emitting the legal cells would
hide it.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cases" / "generated" / "conversion"

#: Source values. Deliberately includes shapes that are valid for some targets
#: and not others -- pairs convert to a dict, a flat string does not.
SOURCES: dict[str, str] = {
    "list-int": "[3, 1, 2]",
    "list-str": "['c', 'a', 'b']",
    "list-mixed": "[1, 'a', 2.5]",
    "list-pairs": "[('a', 1), ('b', 2)]",
    "list-nested": "[[1, 2], [3, 4]]",
    "tuple-int": "(3, 1, 2)",
    "str": "'cab'",
    "dict": "{'b': 2, 'a': 1}",
    "set-int": "{3, 1, 2}",
    "range": "range(3)",
    "empty-list": "[]",
    "generator": "(v for v in (3, 1, 2))",
}

#: Each constructor consumes `src`. Sort-based ones use a key so a
#: heterogeneous source does not raise under CPython -- the point is the
#: conversion, not comparison rules.
CONSTRUCTORS: dict[str, str] = {
    "list": "print(list(src))",
    "tuple": "print(tuple(src))",
    "set": "print(sorted(set(src), key=repr))",
    "frozenset": "print(sorted(frozenset(src), key=repr))\nprint(type(frozenset(src)).__name__)",
    "dict": "print(sorted(dict(src).items(), key=repr))",
    "sorted": "print(sorted(src, key=repr))",
    "reversed-list": "print(list(reversed(list(src))))",
    "len": "print(len(list(src)))",
    "iter-next": "it = iter(src)\nprint(next(it))",
    "unpack-star": "print([*src])",
    "join-repr": "print('|'.join(repr(v) for v in src))",
    "enumerate-list": "print(list(enumerate(src)))",
}

TEMPLATE = """# tier: spec
# ref: library/stdtypes.html#iterator-types
src = {source}
try:
{body}
except TypeError:
    print('TypeError')
except ValueError:
    print('ValueError')
except StopIteration:
    # next() on an empty source. A legitimate outcome for one cell of the
    # product rather than a broken case, so it is recorded like the others.
    print('StopIteration')
"""


def main() -> int:
    written = 0
    for ctor, body in CONSTRUCTORS.items():
        indented = "\n".join("    " + line for line in body.split("\n"))
        for name, literal in SOURCES.items():
            path = OUT / ctor / f"{name}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                TEMPLATE.format(source=literal, body=indented), encoding="utf-8"
            )
            written += 1
    print(f"gen_conversion: {written} cases "
          f"({len(CONSTRUCTORS)} constructors x {len(SOURCES)} sources) -> {OUT}")
    print("now run: python conformance/regen.py --groups generated/conversion")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
