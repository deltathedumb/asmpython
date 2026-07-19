from __future__ import annotations

import unittest

from asmpython._backends.x86_64.codegen import FuncCodegen
from asmpython._backends.x86_64.encoder import (
    Mem, Reg, encode_call_rel32, encode_mov_mr, encode_mov_ri,
)
from asmpython._backends.x86_64.regalloc import AllocResult, RegLoc
from asmpython._compiler.ir import I64, PTR, IRBlock, IRFunc, IRInstr, IRValue


class SysvVarargCallTests(unittest.TestCase):
    def test_rax_argument_is_captured_before_al_vector_count_write(self) -> None:
        fmt = IRValue("fmt", PTR)
        first = IRValue("first", PTR)
        second = IRValue("second", PTR)
        third = IRValue("third", PTR)
        func = IRFunc("probe", [], I64, [IRBlock("entry")])
        alloc = AllocResult(
            locs={
                fmt.name: RegLoc(Reg.RAX),
                first.name: RegLoc(Reg.RBX),
                second.name: RegLoc(Reg.R12),
                third.name: RegLoc(Reg.R13),
            },
            alloca_slots={}, stack_bytes=0, callee_saved=[], callee_saved_xmm=[],
        )
        generator = FuncCodegen(func, alloc, "sysv")
        generator._call(IRInstr("call", None, ["printf", fmt, first, second, third]))
        code = bytes(generator.buf)

        capture_at = code.index(encode_mov_mr(Mem(Reg.RSP, 0), Reg.RAX))
        vector_count_at = code.index(encode_mov_ri(Reg.RAX, 0))
        call_at = code.index(encode_call_rel32(0))
        self.assertLess(capture_at, vector_count_at)
        self.assertLess(vector_count_at, call_at)


if __name__ == "__main__":
    unittest.main()
