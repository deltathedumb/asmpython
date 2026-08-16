from __future__ import annotations

import unittest

from asmpython._backends.arm64.codegen import compile_func
from asmpython._backends.arm64.encoder import Reg, VReg
from asmpython._backends.arm64.regalloc import (
    AllocResult,
    RegLoc,
    StackLoc,
    _FP_POOL,
    _GP_POOL,
)
from asmpython._compiler.ssa.ir import I64, PTR, IRBlock, IRFunc, IRInstr, IRValue


class Arm64CodegenTests(unittest.TestCase):
    def test_codegen_scratch_registers_are_not_allocatable(self) -> None:
        self.assertTrue({Reg.X13, Reg.X14, Reg.X15}.isdisjoint(_GP_POOL))
        self.assertTrue({VReg.V14, VReg.V15}.isdisjoint(_FP_POOL))

    def test_minimal_integer_add_matches_real_aarch64_assembler(self) -> None:
        a = IRValue("a", I64)
        b = IRValue("b", I64)
        result = IRValue("result", I64)
        func = IRFunc(
            "add2",
            [a, b],
            I64,
            [
                IRBlock(
                    "entry",
                    [
                        IRInstr("iadd", result, [a, b]),
                        IRInstr("ret", None, [result]),
                    ],
                )
            ],
        )
        alloc = AllocResult(
            locs={
                "a": RegLoc(Reg.X0),
                "b": RegLoc(Reg.X1),
                "result": RegLoc(Reg.X9),
            },
            alloca_slots={},
            stack_bytes=0,
            callee_saved=[],
            callee_saved_fp=[],
        )

        code = compile_func(func, alloc).code

        # Independently assembled with:
        #   clang --target=aarch64-linux-gnu -c add2.s
        expected = bytes.fromhex(
            "fd7bbfa9"  # stp x29, x30, [sp, #-16]!
            "fd030091"  # add x29, sp, #0
            "0900018b"  # add x9, x0, x1
            "e00309aa"  # mov x0, x9
            "bf030091"  # add sp, x29, #0
            "fd7bc1a8"  # ldp x29, x30, [sp], #16
            "c0035fd6"  # ret
        )
        self.assertEqual(code, expected)

    def test_alloca_store_load_matches_real_aarch64_assembler(self) -> None:
        slot = IRValue("slot", PTR)
        value = IRValue("value", I64)
        loaded = IRValue("loaded", I64)
        func = IRFunc(
            "stack_roundtrip",
            [],
            I64,
            [
                IRBlock(
                    "entry",
                    [
                        IRInstr("alloca", slot, [8]),
                        IRInstr("const", value, [42]),
                        IRInstr("store", None, [value, slot]),
                        IRInstr("load", loaded, [slot]),
                        IRInstr("ret", None, [loaded]),
                    ],
                )
            ],
        )
        alloc = AllocResult(
            locs={
                "value": RegLoc(Reg.X9),
                "loaded": RegLoc(Reg.X10),
            },
            alloca_slots={"slot": -8},
            stack_bytes=16,
            callee_saved=[],
            callee_saved_fp=[],
        )

        code = compile_func(func, alloc).code

        expected = bytes.fromhex(
            "fd7bbfa9"  # stp x29, x30, [sp, #-16]!
            "fd030091"  # add x29, sp, #0
            "ff4300d1"  # sub sp, sp, #16
            "4d0580d2"  # movz x13, #42
            "e9030daa"  # mov x9, x13
            "ad2300d1"  # sub x13, x29, #8
            "a90100f9"  # str x9, [x13]
            "ad2300d1"  # sub x13, x29, #8
            "aa0140f9"  # ldr x10, [x13]
            "e0030aaa"  # mov x0, x10
            "bf030091"  # add sp, x29, #0
            "fd7bc1a8"  # ldp x29, x30, [sp], #16
            "c0035fd6"  # ret
        )
        self.assertEqual(code, expected)

    def test_negative_spill_slot_uses_materialized_address(self) -> None:
        value = IRValue("value", I64)
        func = IRFunc(
            "spill7",
            [],
            I64,
            [
                IRBlock(
                    "entry",
                    [
                        IRInstr("const", value, [7]),
                        IRInstr("ret", None, [value]),
                    ],
                )
            ],
        )
        alloc = AllocResult(
            locs={"value": StackLoc(-8)},
            alloca_slots={},
            stack_bytes=16,
            callee_saved=[],
            callee_saved_fp=[],
        )

        code = compile_func(func, alloc).code

        expected = bytes.fromhex(
            "fd7bbfa9"  # stp x29, x30, [sp, #-16]!
            "fd030091"  # add x29, sp, #0
            "ff4300d1"  # sub sp, sp, #16
            "ed0080d2"  # movz x13, #7
            "af2300d1"  # sub x15, x29, #8
            "ed0100f9"  # str x13, [x15]
            "af2300d1"  # sub x15, x29, #8
            "ed0140f9"  # ldr x13, [x15]
            "e0030daa"  # mov x0, x13
            "bf030091"  # add sp, x29, #0
            "fd7bc1a8"  # ldp x29, x30, [sp], #16
            "c0035fd6"  # ret
        )
        self.assertEqual(code, expected)


if __name__ == "__main__":
    unittest.main()
