from __future__ import annotations

from pathlib import Path


IR_OLD = '''        iter_t = A.expr_type(s.iter)
        if iter_t not in ("list", "dict", "str", "any"):
            raise LowerError(f"unsupported stmt For (iterating {iter_t!r})")
        if iter_t == "dict":
            el_ty = "str"
        elif iter_t == "str":
            el_ty = "str"
        elif iter_t == "any":
            el_ty = "any"
        elif isinstance(s.iter, A.ListLit):
            el_ty = s.iter.el_type
'''

IR_NEW = '''        iter_t = A.expr_type(s.iter)
        if iter_t not in ("list", "tuple", "dict", "str", "any"):
            raise LowerError(f"unsupported stmt For (iterating {iter_t!r})")
        if iter_t == "dict":
            el_ty = "str"
        elif iter_t == "str":
            el_ty = "str"
        elif iter_t == "any":
            el_ty = "any"
        elif iter_t == "tuple":
            tuple_types = A.tuple_element_types(s.iter)
            el_ty = tuple_types[0] if tuple_types else "int"
        elif isinstance(s.iter, A.ListLit):
            el_ty = s.iter.el_type
'''

FLOAT_GUARD_OLD = '''        if el_ty == "float":
            raise LowerError("unsupported stmt For (float list elements)")
'''

FLOAT_GUARD_NEW = '''        if el_ty == "float" and iter_t != "tuple":
            raise LowerError("unsupported stmt For (float list elements)")
'''

FLOAT_HELPER = r'''
; rax = float_to_str(xmm0) -> a fresh nul-terminated Python-style float
; representation. Search the minimum fixed/scientific precision whose text
; round-trips through strtod to the exact original IEEE-754 bits. This is the
; SysV port of the Windows runtime's shortest-roundtrip formatter.
_abi_float_to_str:
    ; SysV entry rsp % 16 == 8. Two pushes keep it at 8; subtracting 40
    ; aligns rsp to 16 before every libc/runtime call below.
    push rbx
    push r12
    sub rsp, 40

    ucomisd xmm0, xmm0
    jp .float_is_nan
    movq rax, xmm0
    mov r10, 0x7FFFFFFFFFFFFFFF
    and rax, r10
    mov r10, 0x7FF0000000000000
    cmp rax, r10
    jne .float_finite
    movq rax, xmm0
    test rax, rax
    jns .float_pos_inf
    lea rax, [_abi_str_ninf]
    jmp .float_done
.float_pos_inf:
    lea rax, [_abi_str_pinf]
    jmp .float_done
.float_is_nan:
    lea rax, [_abi_str_nan]
    jmp .float_done

.float_finite:
    movsd [rsp], xmm0
    movq rax, xmm0
    mov r10, 0x7FFFFFFFFFFFFFFF
    and rax, r10
    movq xmm1, rax
    xorpd xmm2, xmm2
    ucomisd xmm1, xmm2
    je .float_notation_fixed
    mov r10, 0x3F1A36E2EB1C432D
    movq xmm3, r10
    ucomisd xmm1, xmm3
    jb .float_notation_sci
    mov r10, 0x4341C37937E08000
    movq xmm3, r10
    ucomisd xmm1, xmm3
    jae .float_notation_sci
.float_notation_fixed:
    mov qword [rsp+8], 0
    jmp .float_search_init
.float_notation_sci:
    mov qword [rsp+8], 1

.float_search_init:
    xor r12d, r12d
.float_search_loop:
    mov [rsp+16], r12
    cmp qword [rsp+8], 0
    je .float_use_fixed_fmt
    lea rbx, [_abi_fmt_sci_buf]
    mov byte [rbx], '%'
    mov byte [rbx+1], '.'
    jmp .float_fmt_digits
.float_use_fixed_fmt:
    lea rbx, [_abi_fmt_fixed_buf]
    mov byte [rbx], '%'
    mov byte [rbx+1], '.'
.float_fmt_digits:
    mov rax, r12
    mov r10, 10
    xor edx, edx
    div r10
    test rax, rax
    jz .float_one_digit
    add al, '0'
    mov [rbx+2], al
    add dl, '0'
    mov [rbx+3], dl
    lea rcx, [rbx+4]
    jmp .float_fmt_kind
.float_one_digit:
    add dl, '0'
    mov [rbx+2], dl
    lea rcx, [rbx+3]
.float_fmt_kind:
    cmp qword [rsp+8], 0
    je .float_fmt_fixed
    mov byte [rcx], 'e'
    mov byte [rcx+1], 0
    jmp .float_fmt_ready
.float_fmt_fixed:
    mov byte [rcx], 'f'
    mov byte [rcx+1], 0
.float_fmt_ready:
    movsd xmm0, [rsp]
    lea rdi, [_abi_float_search_buf]
    mov rsi, rbx
    mov eax, 1
    call sprintf

    lea rdi, [_abi_float_search_buf]
    xor esi, esi
    call strtod
    movq rax, xmm0
    movsd xmm1, [rsp]
    movq r10, xmm1
    mov r12, [rsp+16]
    cmp rax, r10
    je .float_search_done
    inc r12
    cmp r12, 17
    jbe .float_search_loop

.float_search_done:
    lea rax, [_abi_float_search_buf]
    mov rbx, rax
.float_exp_scan:
    mov cl, [rbx]
    test cl, cl
    jz .float_no_exp
    cmp cl, 'e'
    je .float_found_exp
    inc rbx
    jmp .float_exp_scan
.float_found_exp:
    lea rsi, [rbx+2]
    mov rdi, rsi
.float_skip_zeros:
    mov cl, [rdi]
    cmp cl, '0'
    jne .float_zeros_done
    lea rdx, [rdi+1]
    cmp byte [rdx], 0
    je .float_zeros_done
    mov r10, rdi
.float_count_rest:
    cmp byte [r10], 0
    je .float_count_done
    inc r10
    jmp .float_count_rest
.float_count_done:
    sub r10, rdi
    cmp r10, 2
    jle .float_zeros_done
    inc rdi
    jmp .float_skip_zeros
.float_zeros_done:
    cmp rdi, rsi
    je .float_no_exp
.float_shift_exp:
    mov cl, [rdi]
    mov [rsi], cl
    test cl, cl
    jz .float_no_exp
    inc rdi
    inc rsi
    jmp .float_shift_exp

.float_no_exp:
    lea rax, [_abi_float_search_buf]
    mov rbx, rax
.float_scan_marker:
    mov cl, [rbx]
    test cl, cl
    jz .float_append_point_zero
    cmp cl, '.'
    je .float_fixup_done
    cmp cl, 'e'
    je .float_fixup_done
    inc rbx
    jmp .float_scan_marker
.float_append_point_zero:
    mov byte [rbx], '.'
    mov byte [rbx+1], '0'
    mov byte [rbx+2], 0
.float_fixup_done:
    lea rax, [_abi_float_search_buf]

.float_done:
    call _runtime_str_concat_dup
    add rsp, 40
    pop r12
    pop rbx
    ret

'''

TEST_SOURCE = '''from __future__ import annotations

import unittest

from asmpython._compiler import ir_lower
from asmpython._compiler.errors import SemaError
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze


def _lower(source: str):
    module = Parser(Lexer(source).tokenize(), frozenset()).parse()
    sema_analyze(module)
    return ir_lower.lower_module(module)


def _instructions(module):
    return [
        instr
        for func in module.funcs
        for block in func.blocks
        for instr in block.instrs
    ]


class TupleIterationLoweringTests(unittest.TestCase):
    def test_homogeneous_int_tuple_uses_list_header_iteration(self) -> None:
        lowered = _lower(
            "nums = (0, 1, 2)\\n"
            "total = 0\\n"
            "for value in nums:\\n"
            "    total += value\\n"
        )
        instructions = _instructions(lowered)
        self.assertTrue(any(instr.op == "gep" and instr.operands[-1] == 8 for instr in instructions))
        self.assertTrue(any(instr.op == "gep" and instr.operands[-1] == 16 for instr in instructions))

    def test_homogeneous_float_tuple_loads_float_cells(self) -> None:
        lowered = _lower(
            "nums = (1.5, 2.5)\\n"
            "total = 0.0\\n"
            "for value in nums:\\n"
            "    total += value\\n"
        )
        loads = [
            instr
            for instr in _instructions(lowered)
            if instr.op == "load" and instr.result is not None
        ]
        self.assertTrue(any(instr.result.type.name == "f64" for instr in loads))

    def test_heterogeneous_tuple_iteration_remains_rejected(self) -> None:
        with self.assertRaisesRegex(SemaError, "heterogeneous tuple"):
            _lower(
                "items = (1, \\\"two\\\")\\n"
                "for item in items:\\n"
                "    print(item)\\n"
            )


if __name__ == "__main__":
    unittest.main()
'''


def replace_once(text: str, before: str, after: str, description: str) -> str:
    if before in text:
        return text.replace(before, after, 1)
    if after in text:
        return text
    raise RuntimeError(f"{description} insertion point changed")


def main() -> None:
    ir_path = Path("asmpython/_compiler/ir_lower.py")
    ir_text = ir_path.read_text(encoding="utf-8")
    ir_text = replace_once(ir_text, IR_OLD, IR_NEW, "tuple iterable dispatch")
    ir_text = replace_once(
        ir_text,
        FLOAT_GUARD_OLD,
        FLOAT_GUARD_NEW,
        "float iterable guard",
    )
    ir_path.write_text(ir_text, encoding="utf-8")

    asm_path = Path("asmpython/_runtime/abi_shims_linux.asm")
    asm = asm_path.read_text(encoding="utf-8")
    asm = replace_once(
        asm,
        "extern _runtime_str_concat\n",
        "extern _runtime_str_concat\nextern _runtime_str_concat_dup\n",
        "str concat dup extern",
    )
    asm = replace_once(asm, "extern strtoll\n", "extern strtoll\nextern strtod\n", "strtod extern")
    asm = replace_once(
        asm,
        "global _abi_str_to_int_base\n",
        "global _abi_str_to_int_base\nglobal _abi_float_to_str\n",
        "float formatter export",
    )
    asm = replace_once(
        asm,
        "_con_buf:   resb 32\n",
        "_con_buf:   resb 32\n"
        "_abi_fmt_fixed_buf: resb 8\n"
        "_abi_fmt_sci_buf:   resb 8\n"
        "_abi_float_search_buf: resb 40\n",
        "float formatter buffers",
    )
    asm = replace_once(
        asm,
        '_con_fmt_s:      db "%s", 0\n',
        '_con_fmt_s:      db "%s", 0\n'
        '_abi_str_nan:    db "nan", 0\n'
        '_abi_str_pinf:   db "inf", 0\n'
        '_abi_str_ninf:   db "-inf", 0\n',
        "float formatter constants",
    )
    marker = "; ---- asmlib.hardware: ring-0-only ops, stubbed (unavailable to ring-3\n"
    if "_abi_float_to_str:" not in asm:
        if marker not in asm:
            raise RuntimeError("float formatter text insertion point changed")
        asm = asm.replace(marker, FLOAT_HELPER + marker, 1)
    asm_path.write_text(asm, encoding="utf-8")

    Path("tests/test_tuple_iteration_lowering.py").write_text(
        TEST_SOURCE,
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
