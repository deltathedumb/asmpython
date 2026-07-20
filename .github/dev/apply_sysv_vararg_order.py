from __future__ import annotations

from pathlib import Path


EARLY_WRITE = '''        if is_sysv_vararg:
            # SysV AMD64 passes the number of live vector arguments in AL for
            # variadic calls such as printf/sprintf.
            self._emit(encode_mov_ri(Reg.RAX, xmm_i))

'''

CALL_BOUNDARY = '''        for dst_r, temp_i, typ in gp_dup_slots:
            if typ == "f32":
                self._emit(encode_mov_rm32(dst_r, Mem(Reg.RSP, temp_base + 8 * temp_i)))
            else:
                self._emit(encode_mov_rm(dst_r, Mem(Reg.RSP, temp_base + 8 * temp_i)))

        if is_indirect:
'''

FIXED_BOUNDARY = '''        for dst_r, temp_i, typ in gp_dup_slots:
            if typ == "f32":
                self._emit(encode_mov_rm32(dst_r, Mem(Reg.RSP, temp_base + 8 * temp_i)))
            else:
                self._emit(encode_mov_rm(dst_r, Mem(Reg.RSP, temp_base + 8 * temp_i)))

        if is_sysv_vararg:
            # SysV AMD64 passes the number of live vector arguments in AL for
            # variadic calls such as printf/sprintf. Do this only after every
            # argument has been captured and loaded: register allocation may
            # place an argument (including the format pointer) in RAX, and an
            # earlier write to EAX would silently replace that value with null.
            self._emit(encode_mov_ri(Reg.RAX, xmm_i))

        if is_indirect:
'''

TEST_SOURCE = '''from __future__ import annotations

import unittest

from asmpython._backends.x86_64.codegen import FuncCodegen
from asmpython._backends.x86_64.encoder import (
    Mem,
    Reg,
    encode_call_rel32,
    encode_mov_mr,
    encode_mov_ri,
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
            alloca_slots={},
            stack_bytes=0,
            callee_saved=[],
            callee_saved_xmm=[],
        )
        generator = FuncCodegen(func, alloc, "sysv")
        generator._call(
            IRInstr("call", None, ["printf", fmt, first, second, third])
        )
        code = bytes(generator.buf)

        capture_at = code.index(encode_mov_mr(Mem(Reg.RSP, 0), Reg.RAX))
        vector_count_at = code.index(encode_mov_ri(Reg.RAX, 0))
        call_at = code.index(encode_call_rel32(0))
        self.assertLess(capture_at, vector_count_at)
        self.assertLess(vector_count_at, call_at)


if __name__ == "__main__":
    unittest.main()
'''


def main() -> None:
    path = Path("asmpython/_backends/x86_64/codegen.py")
    text = path.read_text(encoding="utf-8")
    if EARLY_WRITE in text:
        text = text.replace(EARLY_WRITE, "", 1)
    elif "Do this only after every" not in text:
        raise RuntimeError("early SysV AL write changed")

    if CALL_BOUNDARY in text:
        text = text.replace(CALL_BOUNDARY, FIXED_BOUNDARY, 1)
    elif FIXED_BOUNDARY not in text:
        raise RuntimeError("SysV call boundary changed")
    path.write_text(text, encoding="utf-8")

    Path("tests/test_x86_64_sysv_varargs.py").write_text(
        TEST_SOURCE,
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
