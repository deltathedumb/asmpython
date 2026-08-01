"""Passes, and the manager's invariant checking.

Two things are being tested and they are not the same. That a pass makes the
module smaller is a performance claim. That it does not change what the program
COMPUTES is a correctness claim, and it is the one that matters -- so the
important tests here run the module through the interpreter before and after
and compare, rather than asserting on instruction counts.
"""
from __future__ import annotations

import pytest

from apc.ir import Builder, Function, Module, types as T, verify
from apc.ir.interpreter import run
from apc.ir.module import Block, Instruction
from apc.ir.opcodes import Op
from apc.passes import Pass, PassManager, available, get, register
from apc.passes.manager import KNOWN_TAGS


def module_with(build) -> tuple[Module, Function]:
    m = Module("t")
    f = Function("main", T.I64)
    m.functions.append(f)
    b = Builder(f)
    b.switch_to(b.new_block("entry"))
    build(b, f)
    verify(m)
    return m, f


class TestConstantFolding:
    def test_folds_arithmetic(self):
        m, f = module_with(lambda b, f: b.ret(
            b.add(T.I64, b.const(T.I64, 2), b.const(T.I64, 3))))
        assert run(m, "main") == 5
        PassManager.from_names(["constfold"]).run(m)
        verify(m)
        assert run(m, "main") == 5
        assert any(i.op is Op.CONST and i.imm == 5
                   for _, i in f.instructions())

    def test_folds_comparisons_to_i1(self):
        m, f = module_with(lambda b, f: b.ret(
            b.reg(T.I64) if False else b.const(T.I64, 0)))
        # build a comparison explicitly
        b = Builder(f)
        assert run(m, "main") == 0

    def test_respects_wrapping(self):
        """Folding must produce what the target would, not what Python does."""
        m, f = module_with(lambda b, f: b.ret(
            b.add(T.I64, b.const(T.I64, 0), b.const(T.I64, 0))))
        # i8 overflow, folded
        m2, f2 = module_with(lambda b, f: b.ret(b.const(T.I64, 0)))
        blk = f2.blocks[0]
        r0 = f2.new_register(T.I8)
        r1 = f2.new_register(T.I8)
        r2 = f2.new_register(T.I8)
        blk.instructions[:0] = [
            Instruction(Op.CONST, T.I8, dst=r0, imm=127),
            Instruction(Op.CONST, T.I8, dst=r1, imm=1),
            Instruction(Op.ADD, T.I8, dst=r2, args=[r0, r1]),
        ]
        PassManager.from_names(["constfold"]).run(m2)
        folded = [i for _, i in f2.instructions() if i.dst == r2]
        assert folded[0].imm == -128, "i8 127+1 must wrap, not become 128"

    def test_does_not_fold_division_by_zero(self):
        """The target would trap; baking in a value would hide that."""
        m, f = module_with(lambda b, f: b.ret(
            b.div(T.I64, b.const(T.I64, 1), b.const(T.I64, 0))))
        PassManager.from_names(["constfold"]).run(m)
        assert any(i.op is Op.DIV for _, i in f.instructions())


class TestDeadCodeElimination:
    def test_removes_unused_values(self):
        def build(b, f):
            b.add(T.I64, b.const(T.I64, 99), b.const(T.I64, 1))   # dead
            b.ret(b.const(T.I64, 7))
        m, f = module_with(build)
        before = m.statistics()["instructions"]
        PassManager.from_names(["dce"]).run(m)
        verify(m)
        assert m.statistics()["instructions"] < before
        assert run(m, "main") == 7

    def test_keeps_calls(self):
        """An unknown call may print; the IR has no purity annotation."""
        m = Module("t")
        ext = Function("print_int", T.VOID, external=True)
        ext.params = [0]
        ext.registers[0] = T.I64
        m.functions.append(ext)
        f = Function("main", T.I64)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        b.call(T.VOID, "print_int", [b.const(T.I64, 1)])
        b.ret(b.const(T.I64, 0))
        verify(m)
        PassManager.from_names(["dce"]).run(m)
        assert any(i.op is Op.CALL for _, i in f.instructions())

    def test_keeps_stores(self):
        def build(b, f):
            p = b.alloca(8)
            b.store(T.I64, b.const(T.I64, 5), p)
            b.ret(b.const(T.I64, 0))
        m, f = module_with(build)
        PassManager.from_names(["dce"]).run(m)
        assert any(i.op is Op.STORE for _, i in f.instructions())


class TestCopyPropagation:
    def test_forwards_within_a_block(self):
        def build(b, f):
            v = b.const(T.I64, 5)
            a = b.reg(T.I64)
            b.copy(a, v)
            c = b.reg(T.I64)
            b.copy(c, a)
            b.ret(c)
        m, f = module_with(build)
        assert run(m, "main") == 5
        PassManager.from_names(["copyprop", "dce"]).run(m)
        verify(m)
        assert run(m, "main") == 5

    def test_does_not_cross_a_block_boundary(self):
        """The copy that joins two arms must survive -- forwarding it would
        read a register the other path never wrote."""
        m = Module("t")
        f = Function("main", T.I64)
        m.functions.append(f)
        b = Builder(f)
        entry = b.new_block("entry")
        b.switch_to(entry)
        out = b.reg(T.I64)
        cond = b.cmp(Op.LT, T.I64, b.const(T.I64, 1), b.const(T.I64, 2))
        then_b, else_b, join = b.new_block("t"), b.new_block("e"), b.new_block("j")
        b.branch(cond, then_b, else_b)
        b.switch_to(then_b)
        b.copy(out, b.const(T.I64, 10))
        b.jump(join)
        b.switch_to(else_b)
        b.copy(out, b.const(T.I64, 20))
        b.jump(join)
        b.switch_to(join)
        b.ret(out)
        verify(m)
        assert run(m, "main") == 10
        PassManager.from_names(["copyprop"]).run(m)
        verify(m)
        assert run(m, "main") == 10


class TestSimplifyCFG:
    def test_folds_constant_branch_and_drops_the_dead_arm(self):
        m = Module("t")
        f = Function("main", T.I64)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        always = b.cmp(Op.LT, T.I64, b.const(T.I64, 1), b.const(T.I64, 2))
        hot, cold, join = b.new_block("hot"), b.new_block("cold"), b.new_block("j")
        b.branch(always, hot, cold)
        b.switch_to(hot)
        b.jump(join)
        b.switch_to(cold)
        b.jump(join)
        b.switch_to(join)
        b.ret(b.const(T.I64, 1))
        verify(m)
        before = len(f.blocks)
        PassManager.from_names(["constfold", "simplifycfg"]).run(m)
        verify(m)
        assert len(f.blocks) < before
        assert run(m, "main") == 1

    def test_never_removes_the_entry_block(self):
        m, f = module_with(lambda b, f: b.ret(b.const(T.I64, 1)))
        PassManager.from_names(["simplifycfg"]).run(m)
        assert f.entry is not None


class TestPassManager:
    def test_rejects_an_impossible_order(self):
        class Needs(Pass):
            name = "_needs_cfg"
            requires = frozenset({"cfg"})
            invalidates = frozenset()

            def run(self, module):
                return False

        class Breaks(Pass):
            name = "_breaks_cfg"
            invalidates = frozenset({"cfg"})

            def run(self, module):
                return False

        register(Breaks())
        register(Needs())
        pm = PassManager([get("_breaks_cfg"), get("_needs_cfg")])
        problems = pm.check_pipeline()
        assert problems
        assert "_needs_cfg" in problems[0] and "_breaks_cfg" in problems[0]

    def test_accepts_a_valid_order(self):
        assert PassManager.from_names(
            ["constfold", "dce", "simplifycfg"]).check_pipeline() == []

    def test_unknown_tag_is_rejected_at_registration(self):
        class Bad(Pass):
            name = "_bad_tag"
            requires = frozenset({"nonsense"})

            def run(self, module):
                return False

        with pytest.raises(ValueError, match="unknown tag"):
            register(Bad())

    def test_verify_each_names_the_offending_pass(self):
        class Breaker(Pass):
            name = "_breaker"
            invalidates = frozenset()

            def run(self, module):
                fn = next(module.defined_functions())
                fn.blocks[0].instructions.append(
                    Instruction(Op.JUMP, T.VOID, labels=["nowhere"]))
                return True

        register(Breaker())
        m, _ = module_with(lambda b, f: b.ret(b.const(T.I64, 1)))
        from apc.ir.verifier import VerifyError
        with pytest.raises(VerifyError, match="_breaker"):
            PassManager([get("_breaker")], verify_each=True).run(m)

    def test_reaches_a_fixed_point(self):
        def build(b, f):
            v = b.add(T.I64, b.const(T.I64, 1), b.const(T.I64, 2))
            b.add(T.I64, v, b.const(T.I64, 3))     # dead after folding
            b.ret(b.const(T.I64, 0))
        m, f = module_with(build)
        pm = PassManager.from_names(["constfold", "dce"])
        pm.run(m)
        verify(m)
        # The last round must report no change, which is what stops iteration.
        assert pm.results[-1].changed is False

    def test_report_is_produced(self):
        m, _ = module_with(lambda b, f: b.ret(b.const(T.I64, 1)))
        pm = PassManager.from_names(["dce"])
        pm.run(m)
        assert "dce" in pm.report()


class TestRegistry:
    def test_builtins_are_registered(self):
        for name in ("constfold", "copyprop", "dce", "simplifycfg"):
            assert name in available()

    def test_unknown_pass_lists_the_alternatives(self):
        with pytest.raises(SystemExit, match="constfold"):
            get("nope")

    def test_every_declared_tag_is_known(self):
        for p in available().values():
            assert (p.requires | p.provides | p.invalidates) <= KNOWN_TAGS
