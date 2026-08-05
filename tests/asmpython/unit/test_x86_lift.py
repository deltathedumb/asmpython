"""Lifting x86-64 assembly back to IR.

Every case here is a defect the lifter had the first time it was ever
executed. It was written and committed unwired -- 660 lines that had never
run -- and the first function handed to it, a two-argument add with an
ordinary GCC prologue, raised `KeyError: 'rsp'` on its second instruction.
That is worth stating because the code reads as if it works: the hard parts
(flags spanning instructions, sub-register writes, frames) are all present
and all argued for in comments. What was missing was execution.

The bugs that execution found split into two kinds, and the second kind is
why a lifter needs a round trip rather than examples:

  * REFUSALS OF THINGS IT IMPLEMENTS. `jne` cleared the recorded comparison
    before reading it, so every conditional jump reported "no comparison
    reaches it" -- flag tracking blamed for a bug in flag tracking's
    bookkeeping. `sub` stemmed to `su` and missed its own table. Each reads
    as a missing feature and is a typo.

  * WRONG ANSWERS. A float-returning function was lifted as returning %rax,
    which it never writes, so it returned zero. Nothing refused; the program
    ran and printed a plausible number.

The second kind is invisible to any test that asks "did it lift", which is
why `test_x86_lift_roundtrip.py` exists alongside this file and runs the
generated corpus through backend and lifter and compares what they print.
"""
from __future__ import annotations

import pytest

from asmpython.diagnostics import DiagnosticSink, SourceFile
from asmpython.frontends.x86.lift import (
    LiftError, Lifter, lift_module, split_functions,
)
from asmpython.frontends.x86.parse import parse
from asmpython.ir import types as T
from asmpython.ir.interpreter import Interpreter
from asmpython.ir.verifier import verify


def statements(text: str):
    sink = DiagnosticSink()
    out = parse(SourceFile(text), sink)
    assert out is not None, [d.message for d in sink.diagnostics]
    return out


def lift_one(body: str, name: str = "f", declared=None):
    """Lift a single function whose body is `body`, prologue included."""
    stmts = statements(f"\t.text\n\t.globl {name}\n{name}:\n{body}")
    texts = split_functions(stmts, {name})
    assert len(texts) == 1, texts
    return Lifter(texts[0], set(), "sysv", declared or {}).run()


def run_one(body: str, args=None, name: str = "f"):
    from asmpython.ir import Module

    fn = lift_one(body, name)
    module = Module("t")
    module.functions.append(fn)
    assert verify(module) is None
    return Interpreter(module).run(name, args or [])


PROLOGUE = "\tpushq %rbp\n\tmovq %rsp, %rbp\n"
EPILOGUE = "\tmovq %rbp, %rsp\n\tpopq %rbp\n\tret\n"


class TestTheFrameIsNotComputed:
    """%rsp and %rbp are bookkeeping for a layout already resolved.

    The lifter models a frame as one `alloca`, so the prologue does not
    describe a computation to lift -- it describes a decision the lifter has
    already made. It has to consume those instructions by name, and the first
    time it ran it did not: `movq %rsp, %rbp` looked like an ordinary move,
    read a register that deliberately has no IR counterpart, and raised
    KeyError from inside a dictionary lookup.
    """

    def test_an_ordinary_prologue_lifts(self):
        fn = lift_one(PROLOGUE + "\tsubq $16, %rsp\n"
                      "\tmovq %rdi, %rax\n" + EPILOGUE)
        assert fn.name == "f"

    def test_leave_is_an_epilogue(self):
        assert run_one(PROLOGUE + "\tmovq $7, %rax\n\tleave\n\tret\n") == 7

    def test_a_frame_slot_round_trips(self):
        body = (PROLOGUE + "\tsubq $16, %rsp\n"
                "\tmovq %rdi, -8(%rbp)\n"
                "\tmovq -8(%rbp), %rax\n" + EPILOGUE)
        assert run_one(body, [42]) == 42

    def test_a_callee_saved_slot_below_the_reservation_is_in_the_frame(self):
        """The backend saves %rbx at -(frame + 8), BELOW what `subq` reserved.

        Sizing the frame from the `subq` immediate alone therefore refused
        every function that touched a callee-saved register, with a message
        that was accurate about the reservation and wrong about the frame.
        """
        body = (PROLOGUE + "\tsubq $16, %rsp\n"
                "\tmovq %rbx, -24(%rbp)\n"
                "\tmovq %rdi, %rbx\n"
                "\tmovq %rbx, %rax\n"
                "\tmovq -24(%rbp), %rbx\n" + EPILOGUE)
        assert run_one(body, [5]) == 5

    def test_rsp_as_a_value_is_a_diagnostic_not_a_keyerror(self):
        with pytest.raises(LiftError) as exc:
            lift_one(PROLOGUE + "\tmovq %rsp, %rax\n" + EPILOGUE)
        assert "%rsp" in exc.value.message

    def test_reading_above_rbp_is_refused(self):
        """16(%rbp) is the caller's frame, which a lifted function has none of."""
        with pytest.raises(LiftError) as exc:
            lift_one(PROLOGUE + "\tmovq 24(%rbp), %rax\n" + EPILOGUE)
        assert "caller" in exc.value.message

    def test_the_outgoing_argument_area_is_refused(self):
        with pytest.raises(LiftError) as exc:
            lift_one(PROLOGUE + "\tsubq $16, %rsp\n"
                     "\tmovq %rdi, 0(%rsp)\n" + EPILOGUE)
        assert "outgoing-argument" in exc.value.message


class TestFlagsSurviveTheInstructionThatReadsThem:
    """`jcc` and `setcc` consume EFLAGS; they do not clobber it.

    The lifter cleared its recorded comparison for every mnemonic outside a
    list of flag-preserving ones, and that list held only instructions that
    ignore flags -- not the ones that read them. So `cmpq` recorded a
    comparison and the `jle` on the next line deleted it before asking what it
    compared. Every conditional jump and every `setcc` in the compiler's own
    output was refused, and the message ("no comparison reaches it, either an
    instruction between the compare and here clobbers the flags, or the
    compare is in another block") pointed at two causes that were both wrong.
    """

    def test_a_conditional_jump_reads_the_compare_above_it(self):
        body = (PROLOGUE
                + "\tcmpq $10, %rdi\n"
                  "\tjl .Lsmall\n"
                  "\tmovq $100, %rax\n"
                + EPILOGUE
                + ".Lsmall:\n\tmovq $1, %rax\n" + EPILOGUE)
        assert run_one(body, [3]) == 1
        assert run_one(body, [30]) == 100

    def test_setcc_reads_the_compare_above_it(self):
        body = (PROLOGUE
                + "\tcmpq %rsi, %rdi\n"
                  "\tsetl %al\n"
                  "\tmovzbq %al, %rax\n" + EPILOGUE)
        assert run_one(body, [1, 2]) == 1
        assert run_one(body, [2, 1]) == 0

    def test_two_setcc_share_one_comparison(self):
        """`sete` then `setnp` is one comparison read twice, and the first
        must not consume it -- this is how a float `==` is compiled."""
        body = (PROLOGUE
                + "\tucomisd %xmm1, %xmm0\n"
                  "\tsete %al\n"
                  "\tsetnp %cl\n"
                  "\tandb %cl, %al\n"
                  "\tmovzbq %al, %rax\n" + EPILOGUE)
        assert run_one(body, [1.0, 1.0]) == 1
        assert run_one(body, [1.0, 2.0]) == 0

    def test_an_arithmetic_instruction_still_invalidates(self):
        with pytest.raises(LiftError) as exc:
            lift_one(PROLOGUE + "\tcmpq $1, %rdi\n"
                     "\taddq $1, %rsi\n\tsetl %al\n" + EPILOGUE)
        assert "no comparison reaches it" in exc.value.message

    def test_a_label_invalidates(self):
        """A join has no single predecessor, so nothing recorded is true."""
        with pytest.raises(LiftError) as exc:
            lift_one(PROLOGUE + "\tcmpq $1, %rdi\n"
                     ".Ljoin:\n\tsetl %al\n" + EPILOGUE)
        assert "no comparison reaches it" in exc.value.message


class TestParityIsAboutNaN:
    """After `ucomisd`, ZF is set for equal AND for unordered.

    x86 collapses two answers into one flag, so the compiler reads the parity
    flag to separate them: `sete`+`setnp` is equality, `setne`+`setp` is
    inequality. Neither `p` nor `np` was a condition the lifter knew, so a
    float comparison was refused outright.

    Modelled without a NaN opcode, because NaN is the only value that
    compares unequal to itself.
    """

    def test_nan_is_not_equal_to_itself(self):
        body = (PROLOGUE
                + "\tucomisd %xmm1, %xmm0\n"
                  "\tsete %al\n\tsetnp %cl\n\tandb %cl, %al\n"
                  "\tmovzbq %al, %rax\n" + EPILOGUE)
        nan = float("nan")
        assert run_one(body, [nan, nan]) == 0

    def test_parity_on_an_integer_compare_is_refused(self):
        with pytest.raises(LiftError) as exc:
            lift_one(PROLOGUE + "\tcmpq %rsi, %rdi\n\tsetp %al\n" + EPILOGUE)
        assert "floating-point" in exc.value.message


class TestBitwiseOperationsOnFloats:
    """`xorpd` is how a double is negated, not only how one is zeroed.

    The lifter accepted `xorpd %xmm0, %xmm0` (the zeroing idiom) and refused
    everything else. The backend emits `xorpd` against a mask of
    0x8000000000000000 to flip a sign bit -- 403 times in forty generated
    programs -- so every program with a unary minus on a float was rejected.
    """

    def test_a_sign_mask_negates(self):
        body = (PROLOGUE
                + "\tmovabsq $-9223372036854775808, %r10\n"
                  "\tmovq %r10, %xmm1\n"
                  "\txorpd %xmm1, %xmm0\n" + EPILOGUE)
        assert run_one(body, [2.5]) == -2.5

    def test_the_zeroing_idiom_still_works(self):
        assert run_one(PROLOGUE + "\txorpd %xmm0, %xmm0\n" + EPILOGUE) == 0.0


class TestTheSuffixIsNotAlwaysAWidth:
    """Stripping a trailing q/l/w/b is right for `addq` and wrong for `sub`.

    The letters marking a width are letters mnemonics end in, so `sub` became
    `su`, `shl` became `sh` and `imul` became `imu`, each missing its own
    table and reported as "not lifted" -- for an instruction that is.
    """

    @pytest.mark.parametrize("body,expect", [
        ("\tmovq $10, %rax\n\tsub $3, %rax\n", 7),
        ("\tmovq $10, %rax\n\tsubq $3, %rax\n", 7),
        ("\tmovq $3, %rax\n\tshl $2, %rax\n", 12),
        ("\tmovq $3, %rax\n\timul $5, %rax\n", 15),
    ])
    def test_the_bare_and_suffixed_spellings_agree(self, body, expect):
        assert run_one(PROLOGUE + body + EPILOGUE) == expect


class TestSignaturesAreReadOffTheCode:
    """Assembly records neither arity nor return type.

    A parameter is an ABI argument register read before it is written, and
    the return type is whichever of %rax/%xmm0 the exit wrote last. The
    second was documented and not implemented: every function was lifted as
    returning %rax, so a float-returning function returned whatever %rax was
    initialised to. Zero. No refusal, no crash -- a plausible number.
    """

    def test_arity_comes_from_argument_registers_read_before_written(self):
        fn = lift_one(PROLOGUE + "\tmovq %rdi, %rax\n\taddq %rsi, %rax\n"
                      + EPILOGUE)
        assert len(fn.params) == 2

    def test_a_register_written_before_read_is_not_a_parameter(self):
        fn = lift_one(PROLOGUE + "\tmovq $1, %rdi\n\tmovq %rdi, %rax\n"
                      + EPILOGUE)
        assert fn.params == []

    def test_a_float_function_returns_xmm0(self):
        fn = lift_one(PROLOGUE + "\tmulsd %xmm1, %xmm0\n" + EPILOGUE)
        assert fn.ret is T.F64
        assert run_one(PROLOGUE + "\tmulsd %xmm1, %xmm0\n" + EPILOGUE,
                       [1.5, 4.0]) == 6.0

    def test_an_int_function_returns_rax(self):
        fn = lift_one(PROLOGUE + "\tmovq %rdi, %rax\n" + EPILOGUE)
        assert fn.ret is T.I64


class TestAModuleNeedsTwoPasses:
    """A call names a symbol and carries no arity.

    So lifting a call site needs the callee's signature, and in a file where
    two functions call each other there is no order in which both are already
    lifted. Signatures are inferred for every function first, bodies second.
    """

    SOURCE = """
\t.text
\t.globl entry
entry:
\tpushq %rbp
\tmovq %rsp, %rbp
\tmovq $4, %rdi
\tmovq $5, %rsi
\tcall addup
\tmovq %rbp, %rsp
\tpopq %rbp
\tret
addup:
\tpushq %rbp
\tmovq %rsp, %rbp
\tmovq %rdi, %rax
\taddq %rsi, %rax
\tmovq %rbp, %rsp
\tpopq %rbp
\tret
"""

    def test_a_call_to_a_function_defined_later_passes_its_arguments(self):
        sink = DiagnosticSink()
        module = lift_module(statements(self.SOURCE), sink)
        assert module is not None, [d.message for d in sink.diagnostics]
        assert verify(module) is None
        assert Interpreter(module).run("entry") == 9

    def test_an_undeclared_external_is_refused(self):
        sink = DiagnosticSink()
        source = ("\t.text\n\t.globl entry\nentry:\n" + PROLOGUE
                  + "\tcall mystery\n" + EPILOGUE)
        assert lift_module(statements(source), sink) is None
        assert "signature is not known" in sink.diagnostics[0].message

    def test_a_declared_external_becomes_an_import(self):
        sink = DiagnosticSink()
        source = ("\t.text\n\t.globl entry\nentry:\n" + PROLOGUE
                  + "\tmovq $65, %rdi\n\tcall putchar\n" + EPILOGUE)
        module = lift_module(statements(source), sink,
                             declared={"putchar": (T.I64, ("rdi",))})
        assert module is not None, [d.message for d in sink.diagnostics]
        putchar = module.function("putchar")
        assert putchar is not None and putchar.external
        assert len(putchar.params) == 1

    def test_mixed_int_and_float_arguments_use_both_sequences(self):
        """System V indexes its integer and SSE sequences independently.

        Rebuilding `f(int, float)` from "two arguments, the second a float"
        picks xmm1 under Microsoft x64 and xmm0 under System V, so the
        declaration names registers rather than a count.
        """
        source = """
\t.text
\t.globl entry
entry:
\tpushq %rbp
\tmovq %rsp, %rbp
\tmovq $3, %rdi
\tmovabsq $4613937818241073152, %r10
\tmovq %r10, %xmm0
\tcall mix
\tmovq %rbp, %rsp
\tpopq %rbp
\tret
mix:
\tpushq %rbp
\tmovq %rsp, %rbp
\tcvtsi2sdq %rdi, %xmm1
\taddsd %xmm1, %xmm0
\tcvttsd2si %xmm0, %rax
\tmovq %rbp, %rsp
\tpopq %rbp
\tret
"""
        sink = DiagnosticSink()
        module = lift_module(statements(source), sink)
        assert module is not None, [d.message for d in sink.diagnostics]
        assert verify(module) is None
        assert Interpreter(module).run("entry") == 6

    def test_exported_symbols_come_from_globl(self):
        sink = DiagnosticSink()
        module = lift_module(statements(self.SOURCE), sink)
        from asmpython.ir.module import Linkage
        assert module.function("entry").linkage is Linkage.EXPORT
        assert module.function("addup").linkage is Linkage.INTERNAL


class TestTheConventionIsToldToTheLifter:
    """Which ABI the assembly was emitted for cannot be read off the text.

    These run both conventions on every host. The round-trip test can only
    exercise the one its own platform compiles for, so on Linux the Microsoft
    x64 path would otherwise never execute -- and it is the path with the
    rule that is easy to get wrong.
    """

    #: `f(int, float)`: the int arrives in the first integer register and the
    #: float in the SSE register for its POSITION under Microsoft x64, and in
    #: the first SSE register under System V.
    MIXED = """
\t.text
\t.globl entry
entry:
\tpushq %rbp
\tmovq %rsp, %rbp
\tmovq $3, {int0}
\tmovabsq $4613937818241073152, %r10
\tmovq %r10, {flt}
\tcall mix
\tmovq %rbp, %rsp
\tpopq %rbp
\tret
mix:
\tpushq %rbp
\tmovq %rsp, %rbp
\tcvtsi2sdq {int0}, %xmm3
\taddsd %xmm3, {flt}
\tcvttsd2si {flt}, %rax
\tmovq %rbp, %rsp
\tpopq %rbp
\tret
"""

    @pytest.mark.parametrize("abi,int0,flt", [
        ("sysv", "%rdi", "%xmm0"),
        ("win64", "%rcx", "%xmm1"),
    ])
    def test_a_mixed_call_passes_both_arguments(self, abi, int0, flt):
        """Microsoft x64 skips %xmm0 for a float in argument position two.

        Scanning the integer and SSE tables independently -- correct for
        System V -- found %xmm0 unused, concluded the function took no float
        arguments, and dropped the parameter. The caller then passed one
        argument to a function taking two and the callee read a register
        initialised to zero. Every generated program with a mixed-type call
        printed a number wrong in its fractional digits only, because the
        integer half had arrived.
        """
        source = self.MIXED.format(int0=int0, flt=flt)
        sink = DiagnosticSink()
        module = lift_module(statements(source), sink, abi=abi)
        assert module is not None, [d.message for d in sink.diagnostics]
        assert len(module.function("mix").params) == 2
        assert verify(module) is None
        assert Interpreter(module).run("entry") == 6


class TestDataBecomesGlobals:
    def test_a_quad_is_eight_bytes(self):
        sink = DiagnosticSink()
        module = lift_module(statements(
            "\t.data\nvalue:\n\t.quad 7\n\t.text\n"), sink)
        g = module.global_("value")
        assert g is not None and g.data == (7).to_bytes(8, "little")

    def test_a_string_is_terminated(self):
        sink = DiagnosticSink()
        module = lift_module(statements(
            '\t.section .rodata\nmsg:\n\t.asciz "hi"\n\t.text\n'), sink)
        assert module.global_("msg").data == b"hi\0"

    def test_a_global_is_addressable_from_code(self):
        source = """
\t.data
value:
\t.quad 41
\t.text
\t.globl entry
entry:
\tpushq %rbp
\tmovq %rsp, %rbp
\tmovq value(%rip), %rax
\taddq $1, %rax
\tmovq %rbp, %rsp
\tpopq %rbp
\tret
"""
        sink = DiagnosticSink()
        module = lift_module(statements(source), sink)
        assert module is not None, [d.message for d in sink.diagnostics]
        assert Interpreter(module).run("entry") == 42


class TestWidthsAreModelled:
    """%eax and %rax are one register, and writing the narrow one is not the
    same as writing the wide one: %eax ZEROES the top half while %ax keeps
    it. Getting the first wrong yields a value correct until it exceeds 32
    bits, which is the kind of bug that survives a test suite."""

    def test_writing_eax_clears_the_top_half(self):
        body = (PROLOGUE
                + "\tmovabsq $4294967296, %rax\n"
                  "\tmovl $5, %eax\n" + EPILOGUE)
        assert run_one(body) == 5

    def test_writing_al_preserves_the_bits_above_it(self):
        body = (PROLOGUE
                + "\tmovq $256, %rax\n"
                  "\tmovb $5, %al\n" + EPILOGUE)
        assert run_one(body) == 261

    def test_a_high_byte_register_is_refused(self):
        with pytest.raises(LiftError) as exc:
            lift_one(PROLOGUE + "\tmovb $1, %ah\n" + EPILOGUE)
        assert "8-15" in exc.value.message
