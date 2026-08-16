"""The x86 assembly parser.

Every case here is one the parser got wrong at some point, or one that only
looks easy. Assembly has no shortage of syntax that a `split(",")` handles
until the day it does not, and the failures are quiet: a memory operand
shredded into three fragments still produces an `Instr`, just a nonsensical
one, and nothing notices until the lifter emits wrong code.
"""
from __future__ import annotations

from tests import harness

from asmpython.diagnostics import DiagnosticSink, SourceFile
from asmpython.frontends.x86.parse import parse, split_args, strip_comment
from asmpython.frontends.x86.syntax import (
    Directive, Imm, Instr, LabelDef, Mem, Reg, Sym, canonical,
)


def statements(text: str):
    sink = DiagnosticSink()
    out = parse(SourceFile(text), sink)
    assert out is not None, [d.message for d in sink.diagnostics]
    return out


def one(text: str):
    out = statements(text)
    assert len(out) == 1, out
    return out[0]


class TestTheLineIsNotTheStatement:
    def test_several_directives_share_a_line(self):
        """COFF emits four on one line, and dropping three loses the symbol."""
        out = statements(".def add; .scl 2; .type 32; .endef")
        assert [d.name for d in out] == ["def", "scl", "type", "endef"]

    def test_a_label_and_an_instruction_share_a_line(self):
        out = statements("main: ret")
        assert isinstance(out[0], LabelDef) and out[0].name == "main"
        assert isinstance(out[1], Instr) and out[1].mnemonic == "ret"

    def test_a_semicolon_inside_a_string_is_data(self):
        out = one('.asciz "a;b"')
        assert out.args == ['"a;b"']

    def test_a_comment_is_removed(self):
        assert one("movq %rax, %rbx # set up").mnemonic == "movq"

    def test_a_hash_inside_a_string_is_not_a_comment(self):
        assert one('.asciz "a#b"').args == ['"a#b"']

    def test_blank_and_comment_only_lines_vanish(self):
        assert statements("\n  \n# nothing\n") == []


class TestSeparatorsAreWhitespace:
    """GCC uses a tab, asmpython a space, and the assembler neither cares."""

    @harness.cases("gap", [" ", "\t", "  \t "])
    def test_either_separates_the_mnemonic(self, gap):
        ins = one(f"movq{gap}%rax, %rbx")
        assert ins.mnemonic == "movq"
        assert [str(o) for o in ins.operands] == ["%rax", "%rbx"]

    def test_a_tab_does_not_get_swallowed_into_the_mnemonic(self):
        """This shipped: `je\\t.L7` parsed as one instruction named `je\\t.l7`.

        It PARSED, which is why nothing caught it -- an unknown mnemonic is
        legal input to this parser by design, so the wreckage looked like an
        instruction the lifter simply did not implement yet.
        """
        ins = one("je\t.L7")
        assert ins.mnemonic == "je"
        assert ins.operands == [Sym(".L7")]

    def test_operand_case_survives(self):
        """Mnemonics fold to lower case; symbols must not -- .L7 != .l7."""
        assert one("jmp\t.LBB0_3").operands == [Sym(".LBB0_3")]


class TestOperands:
    def test_a_register_carries_its_family_and_width(self):
        assert canonical("r10b") == ("r10", 8)
        assert canonical("eax") == ("rax", 32)
        assert canonical("rsp") == ("rsp", 64)
        ins = one("movzbq %r10b, %r10")
        assert ins.operands[0] == Reg("r10", 8, "r10b")
        assert ins.operands[1] == Reg("r10", 64, "r10")

    def test_the_high_byte_registers_parse(self):
        """%ah is not the low bits of anything, and must not silently become
        %rax at width 8 -- that would read the wrong bits."""
        assert one("movb %ah, %al").operands[0].is_high_byte

    def test_a_negative_immediate(self):
        assert one("movq $-8, %rax").operands[0] == Imm(-8)

    def test_a_hex_immediate(self):
        assert one("movq $0x1F, %rax").operands[0] == Imm(31)

    def test_a_symbolic_immediate(self):
        assert one("movq $greeting, %rax").operands[0] == Imm(None, "greeting")

    def test_a_full_memory_operand(self):
        """Four commas, two operands. Splitting on every comma loses this."""
        ins = one("movq $1, -16(%rbp,%rax,8)")
        assert ins.operands[0] == Imm(1)
        assert ins.operands[1] == Mem(disp=-16, base="rbp", index="rax", scale=8)

    def test_a_bare_indirect(self):
        assert one("movq (%rax), %rdx").operands[0] == Mem(base="rax")

    def test_rip_relative(self):
        mem = one("leaq .LC2(%rip), %rax").operands[0]
        assert mem.symbol == ".LC2" and mem.rip_relative

    def test_an_indirect_call_drops_the_star(self):
        assert one("call *%rax").operands[0] == Reg("rax", 64, "rax")

    def test_a_call_target_is_a_symbol(self):
        assert one("call put_int").operands[0] == Sym("put_int")


class TestItRefusesWhatItCannotRead:
    """A diagnostic, never a traceback -- assembly is user input here."""

    @harness.cases("text,code", [
        ("movq %rax, %nonsense", "E0700"),
        ("movq -8(%rbp, %rax, 3), %rax", "E0704"),
        ("movq -8(%rbp,%rax,8,1), %rax", "E0705"),
        ("movq -8(%rbp, 5), %rax", "E0702"),
    ])
    def test_it_reports(self, text, code):
        sink = DiagnosticSink()
        assert parse(SourceFile(text), sink) is None
        assert code in {d.code for d in sink.diagnostics}

    def test_an_unknown_mnemonic_is_accepted(self):
        """The parser holds no instruction list; that is the lifter's job.

        Refusing here would report a syntax error for something spelled
        perfectly, and send the reader looking at their assembler manual
        instead of at asmpython's coverage.
        """
        assert one("vfmadd231pd %ymm1, %ymm2, %ymm3").mnemonic == "vfmadd231pd"


class TestTheHelpers:
    def test_split_args_respects_parentheses(self):
        assert split_args("$1, -16(%rbp,%rax,8)") == ["$1", "-16(%rbp,%rax,8)"]

    def test_split_args_respects_strings(self):
        assert split_args('"a, b", 3') == ['"a, b"', "3"]

    def test_strip_comment_leaves_strings_alone(self):
        assert strip_comment('.asciz "x # y" # real') == '.asciz "x # y" '

    def test_strip_comment_handles_double_slash(self):
        assert strip_comment("ret // done").strip() == "ret"
