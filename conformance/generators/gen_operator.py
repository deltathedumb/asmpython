"""Generate the operator cross-product: every OPERATOR over every OPERAND PAIR.

    python conformance/generators/gen_operator.py
    python conformance/regen.py --groups generated/operator

The third cross-product. gen_boundary.py moves a value across storage
boundaries; gen_consumer.py varies who reads a container; this one varies what
is DONE to a pair of values.

    cases/generated/operator/<op>/<left>-<right>.py

Operators are where a compiled implementation's representation choices become
observable all at once. `1 + 2.5` has to reconcile a GP register with an xmm
one; `True + 1` has to decide whether bool is int; `'ab' * 3` and `[1] * 3`
share a spelling and share nothing else; `7 in [1, 2]` is a scan, not
arithmetic. A single wrong representation shows up as a whole ROW (one operator
broken for every pair) or a whole COLUMN (one pair broken under every
operator), and those are different bugs.

EVERY PAIR IS EMITTED, including the ones CPython rejects. The body catches
TypeError and ZeroDivisionError and prints the exception name, so
`{'k': 1} - {'k': 1}` is a recordable case whose expected output is
"TypeError". That is deliberate and it is half the value here: an
implementation that silently computes something where CPython raises is
committing a worse error than one that computes the wrong number, and a suite
that only tested legal combinations could never see it.

It also means the generator needs no table of which combinations are legal --
CPython decides, and regen records the verdict. There is nothing to keep in
sync and nothing to get wrong.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cases" / "generated" / "operator"

#: Operand literals. Chosen with no zero divisors, so a ZeroDivisionError in the
#: output means the implementation invented one.
OPERANDS: dict[str, str] = {
    "int": "7",
    "float": "2.5",
    "bool": "True",
    "str": "'ab'",
    # The container literals CONTAIN the scalar ones, so the `contains` row
    # exercises the found path rather than asserting False sixteen times. A
    # membership test that only ever misses never touches the comparison the
    # scan actually performs on each element.
    "list": "[7, 2]",
    "tuple": "(7, 2)",
    "dict": "{'ab': 1}",
    "none": "None",
}

#: Pairs worth crossing. The full 8x8 would be 64 per operator and most of it
#: would be TypeError repeated; these are the pairs where an implementation
#: plausibly diverges -- mixed numeric widths, bool-as-int, the overloaded
#: spellings (* and + on sequences), and None reaching arithmetic.
PAIRS: list[tuple[str, str]] = [
    ("int", "int"),
    ("int", "float"),
    ("float", "int"),
    ("float", "float"),
    ("int", "bool"),
    ("bool", "bool"),
    ("bool", "int"),
    ("str", "str"),
    ("str", "int"),
    ("list", "list"),
    ("list", "int"),
    ("tuple", "tuple"),
    ("int", "none"),
    ("none", "none"),
    ("dict", "dict"),
    ("int", "str"),
    # `in` reads (needle, container), which is the reverse of the arithmetic
    # order, so without these the contains row would be almost entirely
    # TypeError -- 16 cells asserting that `[1, 2] in 7` fails, and not one
    # asserting that `7 in [1, 2]` succeeds. They cost a TypeError cell under
    # each arithmetic operator, which is itself worth pinning.
    ("int", "list"),
    ("int", "tuple"),
    ("str", "dict"),
]

#: Binary operators, as a source template. `in` reads (needle, container), which
#: is the same shape with the operands in the other order -- kept here rather
#: than in its own generator because it fails the same way: a wrong element
#: representation breaks the scan.
OPS: dict[str, str] = {
    "add": "a + b",
    "sub": "a - b",
    "mul": "a * b",
    "truediv": "a / b",
    "floordiv": "a // b",
    "mod": "a % b",
    "pow": "a ** b",
    "eq": "a == b",
    "ne": "a != b",
    "lt": "a < b",
    "le": "a <= b",
    "gt": "a > b",
    "ge": "a >= b",
    "contains": "a in b",
    "and-op": "a and b",
    "or-op": "a or b",
}

TEMPLATE = """# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
a = {left}
b = {right}
try:
    r = {expr}
    print(r)
    print(type(r).__name__)
except TypeError:
    print('TypeError')
except ZeroDivisionError:
    print('ZeroDivisionError')
"""


def main() -> int:
    written = 0
    for op_name, expr in OPS.items():
        for lk, rk in PAIRS:
            path = OUT / op_name / f"{lk}-{rk}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                TEMPLATE.format(
                    left=OPERANDS[lk], right=OPERANDS[rk], expr=expr
                ),
                encoding="utf-8",
            )
            written += 1
    print(f"gen_operator: {written} cases "
          f"({len(OPS)} operators x {len(PAIRS)} operand pairs) -> {OUT}")
    print("now run: python conformance/regen.py --groups generated/operator")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
