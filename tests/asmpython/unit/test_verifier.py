"""The verifier is the contract. Each test here asserts one invariant.

A backend author reads `verifier.py`'s header, assumes every item, and writes
no defensive checks. That is only honest if each item is actually enforced, so
every invariant gets a test that BREAKS it and asserts the verifier notices.

The tests are written as "construct something invalid, expect a specific
complaint" rather than "expect any error", because a verifier that rejects for
the wrong reason is a verifier that will accept the real bug tomorrow.
"""
from __future__ import annotations

import pytest

from asmpython.ir import Builder, Function, Global, Module, types as T, verify
from asmpython.ir.module import Block, Instruction
from asmpython.ir.opcodes import Op
from asmpython.ir.verifier import VerifyError


def one_block(*instructions: Instruction, ret: T.Type = T.VOID,
              regs: dict[int, T.Type] | None = None) -> Module:
    """A module with a single function and a single block."""
    m = Module()
    f = Function("f", ret)
    f.registers.update(regs or {})
    f.blocks = [Block("entry", list(instructions))]
    m.functions.append(f)
    return m


def problems_of(m: Module) -> list[str]:
    with pytest.raises(VerifyError) as exc:
        verify(m)
    return exc.value.problems


def assert_complains(m: Module, *fragments: str) -> None:
    joined = "\n".join(problems_of(m))
    for fragment in fragments:
        assert fragment in joined, f"expected {fragment!r} in:\n{joined}"


# ── 1. every block ends in exactly one terminator ───────────────────────────
class TestTermination:
    def test_missing_terminator(self):
        m = one_block(Instruction(Op.CONST, T.I64, dst=0, imm=1),
                      regs={0: T.I64})
        assert_complains(m, "not a terminator")

    def test_terminator_in_the_middle(self):
        m = one_block(
            Instruction(Op.RET, T.VOID),
            Instruction(Op.CONST, T.I64, dst=0, imm=1),
            regs={0: T.I64})
        assert_complains(m, "is a terminator but is followed by")

    def test_empty_block(self):
        m = one_block()
        assert_complains(m, "empty block")


# ── 2. branch targets exist ─────────────────────────────────────────────────
class TestBranchTargets:
    def test_unknown_label(self):
        m = one_block(Instruction(Op.JUMP, T.VOID, labels=["nowhere"]))
        assert_complains(m, "unknown block 'nowhere'")

    def test_jump_arity(self):
        m = one_block(Instruction(Op.JUMP, T.VOID, labels=[]))
        assert_complains(m, "exactly 1 target")


# ── 3. no register is read before it is written ─────────────────────────────
class TestReadBeforeWrite:
    def test_straight_line(self):
        m = one_block(
            Instruction(Op.ADD, T.I64, dst=0, args=[1, 1]),
            Instruction(Op.RET, T.VOID),
            regs={0: T.I64, 1: T.I64})
        assert_complains(m, "reads %1 before any path writes it")

    def test_written_on_only_one_arm_of_a_branch(self):
        """The invariant mutable registers make necessary.

        Written in `then` but not `else`, then read at the join: on one path
        the register holds whatever was there before. Under SSA this shape is
        structurally impossible; here it is a plain typo.
        """
        m = Module()
        f = Function("f", T.VOID)
        f.registers.update({0: T.I1, 1: T.I64})
        f.blocks = [
            Block("entry", [Instruction(Op.BRANCH, T.I1, args=[0],
                                        labels=["then", "join"])]),
            Block("then", [Instruction(Op.CONST, T.I64, dst=1, imm=1),
                           Instruction(Op.JUMP, T.VOID, labels=["join"])]),
            Block("join", [Instruction(Op.NEG, T.I64, dst=1, args=[1]),
                           Instruction(Op.RET, T.VOID)]),
        ]
        f.params = [0]
        m.functions.append(f)
        assert_complains(m, "reads %1 before any path writes it")

    def test_written_on_both_arms_is_fine(self):
        m = Module()
        f = Function("f", T.VOID)
        f.registers.update({0: T.I1, 1: T.I64})
        f.params = [0]
        f.blocks = [
            Block("entry", [Instruction(Op.BRANCH, T.I1, args=[0],
                                        labels=["then", "els"])]),
            Block("then", [Instruction(Op.CONST, T.I64, dst=1, imm=1),
                           Instruction(Op.JUMP, T.VOID, labels=["join"])]),
            Block("els", [Instruction(Op.CONST, T.I64, dst=1, imm=2),
                          Instruction(Op.JUMP, T.VOID, labels=["join"])]),
            Block("join", [Instruction(Op.NEG, T.I64, dst=1, args=[1]),
                           Instruction(Op.RET, T.VOID)]),
        ]
        m.functions.append(f)
        verify(m)

    def test_loop_carried_value_converges(self):
        """A back edge must not be reported as a use before a definition."""
        m = Module()
        f = Function("f", T.VOID)
        f.registers.update({0: T.I1, 1: T.I64})
        f.params = [0]
        f.blocks = [
            Block("entry", [Instruction(Op.CONST, T.I64, dst=1, imm=0),
                            Instruction(Op.JUMP, T.VOID, labels=["head"])]),
            Block("head", [Instruction(Op.BRANCH, T.I1, args=[0],
                                       labels=["body", "done"])]),
            Block("body", [Instruction(Op.ADD, T.I64, dst=1, args=[1, 1]),
                           Instruction(Op.JUMP, T.VOID, labels=["head"])]),
            Block("done", [Instruction(Op.RET, T.VOID)]),
        ]
        m.functions.append(f)
        verify(m)


# ── 4/5/6. types and operand shapes ─────────────────────────────────────────
class TestTypeDiscipline:
    def test_operand_type_mismatch(self):
        m = one_block(
            Instruction(Op.CONST, T.I64, dst=0, imm=1),
            Instruction(Op.CONST, T.F64, dst=1, imm=1.0),
            Instruction(Op.ADD, T.I64, dst=2, args=[0, 1]),
            Instruction(Op.RET, T.VOID),
            regs={0: T.I64, 1: T.F64, 2: T.I64})
        assert_complains(m, "was given %1 of type f64")

    def test_bitwise_on_float_is_rejected(self):
        m = one_block(
            Instruction(Op.CONST, T.F64, dst=0, imm=1.0),
            Instruction(Op.AND, T.F64, dst=1, args=[0, 0]),
            Instruction(Op.RET, T.VOID),
            regs={0: T.F64, 1: T.F64})
        assert_complains(m, "cannot operate on f64")

    def test_comparison_must_define_i1(self):
        m = one_block(
            Instruction(Op.CONST, T.I64, dst=0, imm=1),
            Instruction(Op.LT, T.I64, dst=1, args=[0, 0]),
            Instruction(Op.RET, T.VOID),
            regs={0: T.I64, 1: T.I64})
        assert_complains(m, "produces i1")

    def test_bitcast_must_preserve_width(self):
        m = one_block(
            Instruction(Op.CONST, T.I64, dst=0, imm=1),
            Instruction(Op.BITCAST, T.I32, dst=1, args=[0]),
            Instruction(Op.RET, T.VOID),
            regs={0: T.I64, 1: T.I32})
        assert_complains(m, "changes width")

    def test_store_address_must_be_a_pointer(self):
        m = one_block(
            Instruction(Op.CONST, T.I64, dst=0, imm=1),
            Instruction(Op.STORE, T.I64, args=[0, 0]),
            Instruction(Op.RET, T.VOID),
            regs={0: T.I64})
        assert_complains(m, "address must be ptr")

    def test_arity(self):
        m = one_block(
            Instruction(Op.CONST, T.I64, dst=0, imm=1),
            Instruction(Op.ADD, T.I64, dst=1, args=[0]),
            Instruction(Op.RET, T.VOID),
            regs={0: T.I64, 1: T.I64})
        assert_complains(m, "takes 2 operand(s), got 1")


# ── 8. symbols resolve ──────────────────────────────────────────────────────
class TestSymbols:
    def test_call_to_unknown_function(self):
        m = one_block(Instruction(Op.CALL, T.VOID, sym="ghost"),
                      Instruction(Op.RET, T.VOID))
        assert_complains(m, "unknown function 'ghost'")

    def test_global_addr_of_unknown_global(self):
        m = one_block(
            Instruction(Op.GLOBAL_ADDR, T.PTR, dst=0, sym="ghost"),
            Instruction(Op.RET, T.VOID),
            regs={0: T.PTR})
        assert_complains(m, "unknown global 'ghost'")

    def test_known_global_is_accepted(self):
        m = one_block(
            Instruction(Op.GLOBAL_ADDR, T.PTR, dst=0, sym="g"),
            Instruction(Op.RET, T.VOID),
            regs={0: T.PTR})
        m.globals.append(Global("g", 8))
        verify(m)


# ── 9. the entry block has no predecessors ──────────────────────────────────
class TestEntryBlock:
    def test_branching_back_to_entry_is_rejected(self):
        """A backend puts the prologue in the entry block, so re-entering it
        would re-run the prologue."""
        m = Module()
        f = Function("f", T.VOID)
        f.blocks = [
            Block("entry", [Instruction(Op.JUMP, T.VOID, labels=["body"])]),
            Block("body", [Instruction(Op.JUMP, T.VOID, labels=["entry"])]),
        ]
        m.functions.append(f)
        assert_complains(m, "branches to the ENTRY block")


# ── 10. returns agree with the signature ────────────────────────────────────
class TestReturns:
    def test_bare_ret_from_a_value_function(self):
        m = one_block(Instruction(Op.RET, T.I64), ret=T.I64)
        assert_complains(m, "ret is bare")

    def test_value_ret_from_a_void_function(self):
        m = one_block(
            Instruction(Op.CONST, T.I64, dst=0, imm=1),
            Instruction(Op.RET, T.VOID, args=[0]),
            regs={0: T.I64})
        assert_complains(m, "returns void but ret has a value")

    def test_wrong_return_type(self):
        m = one_block(
            Instruction(Op.CONST, T.F64, dst=0, imm=1.0),
            Instruction(Op.RET, T.I64, args=[0]),
            ret=T.I64, regs={0: T.F64})
        assert_complains(m, "but ret gives f64")


# ── reporting behaviour ─────────────────────────────────────────────────────
class TestReporting:
    def test_every_problem_is_reported_not_just_the_first(self):
        m = one_block(
            Instruction(Op.JUMP, T.VOID, labels=["nowhere"]),
            Instruction(Op.CONST, T.I64, dst=0, imm=1),
            regs={0: T.I64})
        assert len(problems_of(m)) >= 2

    def test_duplicate_block_labels(self):
        m = Module()
        f = Function("f", T.VOID)
        f.blocks = [Block("x", [Instruction(Op.RET, T.VOID)]),
                    Block("x", [Instruction(Op.RET, T.VOID)])]
        m.functions.append(f)
        assert_complains(m, "duplicate block label")

    def test_external_functions_are_not_checked_for_bodies(self):
        m = Module()
        m.functions.append(Function("ext", T.VOID, external=True))
        verify(m)

    def test_report_is_readable(self):
        m = one_block(Instruction(Op.JUMP, T.VOID, labels=["nowhere"]))
        with pytest.raises(VerifyError) as exc:
            verify(m)
        assert "nowhere" in exc.value.report()
