from __future__ import annotations

import unittest

from asmpython._backends.arm64 import codegen, encoder, regalloc
from asmpython._compiler.ir import I64, IRBlock, IRFunc, IRInstr, IRValue


class Arm64CodegenSmokeTests(unittest.TestCase):
    def test_codegen_import_and_scratch_register_reservations(self) -> None:
        self.assertNotIn(encoder.Reg.X13, regalloc._GP_POOL)
        self.assertNotIn(encoder.Reg.X14, regalloc._GP_POOL)
        self.assertNotIn(encoder.Reg.X15, regalloc._GP_POOL)
        self.assertNotIn(encoder.VReg.V14, regalloc._FP_POOL)
        self.assertNotIn(encoder.VReg.V15, regalloc._FP_POOL)

    def test_simple_add_reaches_machine_code(self) -> None:
        left = IRValue("left", I64)
        right = IRValue("right", I64)
        result = IRValue("result", I64)
        func = IRFunc(
            "add_two",
            [left, right],
            I64,
            [
                IRBlock(
                    "entry",
                    [
                        IRInstr("iadd", result, [left, right]),
                        IRInstr("ret", None, [result]),
                    ],
                )
            ],
        )

        allocation = regalloc.allocate(func)
        compiled = codegen.compile_func(func, allocation)

        self.assertEqual(compiled.name, "add_two")
        self.assertEqual(compiled.relocs, [])
        self.assertGreaterEqual(len(compiled.code), 20)
        self.assertEqual(len(compiled.code) % 4, 0)
        self.assertTrue(compiled.code.endswith(encoder.ret()))

    def test_negative_spill_offsets_use_signed_stack_accesses(self) -> None:
        left = IRValue("left", I64)
        right = IRValue("right", I64)
        result = IRValue("result", I64)
        func = IRFunc(
            "spilled_add",
            [],
            I64,
            [
                IRBlock(
                    "entry",
                    [
                        IRInstr("iadd", result, [left, right]),
                        IRInstr("ret", None, [result]),
                    ],
                )
            ],
        )
        allocation = regalloc.AllocResult(
            locs={
                left.name: regalloc.StackLoc(-8),
                right.name: regalloc.StackLoc(-16),
                result.name: regalloc.StackLoc(-24),
            },
            alloca_slots={},
            stack_bytes=32,
            callee_saved=[],
            callee_saved_fp=[],
        )

        compiled = codegen.compile_func(func, allocation)

        self.assertIn(encoder.ldur(encoder.Reg.X13, encoder.Reg.X29, -8), compiled.code)
        self.assertIn(encoder.ldur(encoder.Reg.X14, encoder.Reg.X29, -16), compiled.code)
        self.assertIn(encoder.stur(encoder.Reg.X13, encoder.Reg.X29, -24), compiled.code)
        self.assertEqual(compiled.relocs, [])

    def test_large_frame_expands_push_and_pop(self) -> None:
        func = IRFunc(
            "large_frame",
            [],
            None,
            [IRBlock("entry", [IRInstr("ret", None, [])])],
        )
        allocation = regalloc.AllocResult(
            locs={},
            alloca_slots={},
            stack_bytes=4608,
            callee_saved=[],
            callee_saved_fp=[],
        )

        compiled = codegen.compile_func(func, allocation)

        # The frame is 4624 bytes including the saved FP/LR record, too large
        # for STP/LDP's signed scaled imm7 writeback form. Both ends therefore
        # expand to explicit SP adjustment sequences around offset-zero pairs.
        self.assertEqual(len(compiled.code), 32)
        self.assertTrue(compiled.code.endswith(encoder.ret()))


if __name__ == "__main__":
    unittest.main()
