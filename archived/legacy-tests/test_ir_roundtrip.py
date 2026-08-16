"""The SSA IR dump is a round-trip format, not a write-only one.

``irfreeze`` serializes the *typed AST* -- the thing ``ir_lower`` consumes --
and round-trips it fine. The neutral SSA ``IRModule`` the backends consume had
no serialized form in either direction, so an IR-level test had to be written
by constructing ``IRInstr`` objects in Python, and a miscompile could not be
bisected by editing the IR.

``ir_print.format_module`` / ``parse_module`` close that: dump it, hand-edit an
instruction, feed it back. The strong property is the last test here -- the
reparsed module must generate byte-identical machine code, which is what makes
an edited dump trustworthy as a reproducer.
"""

from __future__ import annotations

import unittest

from asmpython._backends.x86_64 import __module_backend__ as x86_backend
from asmpython._compiler import driver
from asmpython._compiler.ssa import ir_lower
from asmpython._compiler.ssa.ir_print import IRParseError, format_module, parse_module
from asmpython._compiler.ssa.ir_verify import validate_ir
from asmpython._frontends.apc import emit_module
from asmpython._frontends.apc import parse as apc_parse

APC = """
extern func puts(s: ptr): i32
const K: int = 7

layout H {
    a: bytes[4]
    b: bytes[4]
}

func hot(n: i64, p: H) {
    let acc = 0
    for (i = 0..n) {
        if (i & 1) { acc = acc + i * 4 } else { acc = acc - 1 }
    }
    puts("hi")
    ret acc + (p.a as i64) + K
}: i64

func main() { ret hot(10, 0 as ptr) }: i64
export main
"""

PY = """
def add(a: int, b: int) -> int:
    total = 0
    for i in range(a):
        total = total + i * b
    return total

print(add(4, 3))
"""


def apc_ir():
    return emit_module(apc_parse(APC), APC)


def python_ir():
    module = driver._compile_program(
        PY, source_dir=None, entry_path=None,
        whole_program=True, all_errors=False)
    return ir_lower.lower_module(module)


class RoundTripTests(unittest.TestCase):
    def test_apc_module_round_trips_exactly(self) -> None:
        text = format_module(apc_ir())
        self.assertEqual(text, format_module(parse_module(text)))

    def test_python_module_round_trips_exactly(self) -> None:
        text = format_module(python_ir())
        self.assertEqual(text, format_module(parse_module(text)))

    def test_reparsed_module_still_verifies(self) -> None:
        validate_ir(parse_module(format_module(apc_ir())))

    def test_module_shape_survives(self) -> None:
        original = apc_ir()
        back = parse_module(format_module(original))
        self.assertEqual([f.name for f in back.funcs],
                         [f.name for f in original.funcs])
        self.assertEqual(back.exports, original.exports)
        self.assertEqual([(g.name, g.value) for g in back.data],
                         [(g.name, g.value) for g in original.data])
        self.assertEqual([[b.label for b in f.blocks] for f in back.funcs],
                         [[b.label for b in f.blocks] for f in original.funcs])

    def test_value_names_are_function_scoped(self) -> None:
        """Two functions may each define %t1; they must stay distinct.

        Sharing one name->IRValue map across functions silently swaps their
        types, which round-trips as a *different* module that still verifies.
        """
        back = parse_module(format_module(apc_ir()))
        for func in back.funcs:
            defined = {i.result.name: i.result.type.name
                       for b in func.blocks for i in b.instrs
                       if i.result is not None}
            for block in func.blocks:
                for instr in block.instrs:
                    for op in instr.operands or []:
                        if hasattr(op, "name") and op.name in defined:
                            self.assertEqual(op.type.name, defined[op.name],
                                             f"{func.name}/%{op.name}")

    def test_comments_and_allocation_annotations_are_ignored(self) -> None:
        annotated = format_module(apc_ir(), with_alloc=True, abi="win64")
        plain = format_module(apc_ir())
        self.assertEqual(format_module(parse_module(annotated)),
                         format_module(parse_module(plain)))

    def test_reparsed_module_generates_identical_code(self) -> None:
        original = apc_ir()
        back = parse_module(format_module(original))
        args = {"target_os": "windows", "abi": "win64"}
        self.assertEqual(
            next(iter(x86_backend.compile(original, args).values())),
            next(iter(x86_backend.compile(back, args).values())),
        )

    def test_garbage_is_rejected_with_a_line_number(self) -> None:
        with self.assertRaises(IRParseError) as ctx:
            parse_module("func f() -> i64 {\n  entry:\n}\n%t1: i64 = const 1\n")
        self.assertIn("line 4", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
