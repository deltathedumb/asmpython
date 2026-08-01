"""IR core: types, opcode table, module structure, builder, printer, CFG.

The verifier gets its own file because its tests are the CONTRACT -- each one
asserts an invariant a backend is permitted to assume.
"""
from __future__ import annotations

import pytest

from apc.diagnostics import SourceFile
from apc.ir import Builder, Function, Global, Module, types as T, verify
from apc.ir.cfg import ControlFlowGraph
from apc.ir.module import Block, Instruction, Linkage
from apc.ir.opcodes import SPECS, Op, TERMINATORS
from apc.ir.printer import parse_module, print_module


# ── types ───────────────────────────────────────────────────────────────────
class TestTypes:
    @pytest.mark.parametrize("name,bits,size", [
        ("i1", 1, 1), ("i8", 8, 1), ("i32", 32, 4), ("i64", 64, 8),
        ("u8", 8, 1), ("f32", 32, 4), ("f64", 64, 8), ("ptr", 64, 8),
    ])
    def test_width(self, name, bits, size):
        ty = T.parse(name)
        assert ty.bits == bits
        assert ty.size == size

    def test_signedness_is_on_the_type(self):
        assert T.I32.is_signed and not T.U32.is_signed
        assert not T.F64.is_signed and not T.PTR.is_signed

    def test_void_has_no_width(self):
        with pytest.raises(ValueError):
            _ = T.VOID.bits

    def test_unknown_type_names_the_alternatives(self):
        with pytest.raises(ValueError, match="i64"):
            T.parse("int32")

    def test_types_are_interned(self):
        assert T.parse("i64") is T.I64


# ── opcode table ────────────────────────────────────────────────────────────
class TestOpcodes:
    def test_every_opcode_has_a_spec(self):
        assert set(SPECS) == set(Op)

    def test_terminators_are_marked_consistently(self):
        for op, spec in SPECS.items():
            assert (op in TERMINATORS) is spec.terminator

    def test_comparisons_produce_i1(self):
        for op in (Op.EQ, Op.NE, Op.LT, Op.LE, Op.GT, Op.GE):
            assert SPECS[op].result == "i1"

    def test_no_opcode_encodes_signedness(self):
        """One DIV, not idiv/udiv/fdiv -- signedness is read from `ty`."""
        names = {op.value for op in Op}
        assert "idiv" not in names and "udiv" not in names and "fdiv" not in names
        assert "div" in names


# ── module structure ────────────────────────────────────────────────────────
def build_sum(n: int = 10) -> tuple[Module, Function]:
    """sum(0..n-1) via a loop, with a host call. The workhorse fixture."""
    m = Module("t")
    ext = Function("print_int", T.VOID, external=True)
    ext.params = [0]
    ext.registers[0] = T.I64
    m.functions.append(ext)

    f = Function("main", T.I64)
    m.functions.append(f)
    b = Builder(f)
    b.switch_to(b.new_block("entry"))
    limit = b.const(T.I64, n)
    acc, i = b.reg(T.I64), b.reg(T.I64)
    b.copy(acc, b.const(T.I64, 0))
    b.copy(i, b.const(T.I64, 0))
    loop, body, done = b.new_block("loop"), b.new_block("body"), b.new_block("done")
    b.jump(loop)
    b.switch_to(loop)
    b.branch(b.cmp(Op.LT, T.I64, i, limit), body, done)
    b.switch_to(body)
    b.copy(acc, b.add(T.I64, acc, i))
    b.copy(i, b.add(T.I64, i, b.const(T.I64, 1)))
    b.jump(loop)
    b.switch_to(done)
    b.call(T.VOID, "print_int", [acc])
    b.ret(acc)
    return m, f


class TestModule:
    def test_builds_and_verifies(self):
        m, _ = build_sum()
        verify(m)

    def test_statistics(self):
        m, _ = build_sum()
        stats = m.statistics()
        assert stats["functions"] == 1
        assert stats["externals"] == 1
        assert stats["blocks"] == 4

    def test_lookup(self):
        m, _ = build_sum()
        assert m.function("main") is not None
        assert m.function("absent") is None

    def test_register_type_is_declared_once(self):
        _, f = build_sum()
        for reg, ty in f.registers.items():
            assert f.register_type(reg) is ty

    def test_unknown_register_raises_with_context(self):
        _, f = build_sum()
        with pytest.raises(KeyError, match="never declared"):
            f.register_type(9999)

    def test_call_is_effectful_but_arithmetic_is_not(self):
        """DCE depends on this: an unknown call may print."""
        assert Instruction(Op.CALL, T.VOID, sym="f").has_side_effects
        assert Instruction(Op.STORE, T.I64, args=[0, 1]).has_side_effects
        assert not Instruction(Op.ADD, T.I64, dst=0, args=[1, 2]).has_side_effects

    def test_replace_uses_reports_change(self):
        ins = Instruction(Op.ADD, T.I64, dst=0, args=[1, 2])
        assert ins.replace_uses({1: 7}) is True
        assert ins.args == [7, 2]
        assert ins.replace_uses({99: 5}) is False


# ── builder ─────────────────────────────────────────────────────────────────
class TestBuilder:
    def test_refuses_to_append_after_a_terminator(self):
        m = Module()
        f = Function("f", T.VOID)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        b.ret()
        with pytest.raises(RuntimeError, match="already ends"):
            b.ret()

    def test_spans_are_sticky(self):
        src = SourceFile("a = 1\nb = 2\n", "t.py")
        m = Module()
        f = Function("f", T.VOID)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        b.span = src.span(0, 5)
        first = b.const(T.I64, 1)
        b.span = src.span(6, 11)
        b.const(T.I64, 2)
        b.ret()
        spans = [ins.span.start_loc.line for ins in f.blocks[0].instructions]
        assert spans == [1, 2, 2]

    def test_new_block_labels_are_unique(self):
        m = Module()
        f = Function("f", T.VOID)
        m.functions.append(f)
        b = Builder(f)
        labels = {b.new_block("x").label for _ in range(50)}
        assert len(labels) == 50


# ── text format ─────────────────────────────────────────────────────────────
class TestTextFormat:
    def test_round_trip_is_exact(self):
        m, _ = build_sum()
        m.globals.append(Global("msg", 3, b"hi\x00", readonly=True))
        text = print_module(m)
        assert print_module(parse_module(text)) == text

    def test_parsed_module_verifies(self):
        m, _ = build_sum()
        verify(parse_module(print_module(m)))

    def test_comparison_prints_its_operand_type(self):
        """`i64.lt`, not `i1.lt`: the result type is the thing you know."""
        m, _ = build_sum()
        text = print_module(m)
        assert "i64.lt" in text
        assert "i1.lt" not in text

    def test_export_survives_the_round_trip(self):
        m = Module()
        f = Function("f", T.VOID, linkage=Linkage.EXPORT)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        b.ret()
        assert parse_module(print_module(m)).function("f").linkage is Linkage.EXPORT

    def test_semicolon_inside_a_string_is_not_a_comment(self):
        m = Module()
        m.globals.append(Global("s", 4, b"a;b\x00"))
        assert parse_module(print_module(m)).global_("s").data == b"a;b\x00"


# ── control-flow graph ──────────────────────────────────────────────────────
def diamond_with_loop() -> Function:
    """b0 -> {b1,b2} -> b3 <-> b4 -> b5, plus unreachable b6."""
    f = Function("t", T.VOID)
    f.registers[0] = T.I1
    f.blocks = [Block(f"b{i}") for i in range(7)]
    edges = {0: [1, 2], 1: [3], 2: [3], 3: [4], 4: [3, 5]}
    for i, targets in edges.items():
        op = Op.JUMP if len(targets) == 1 else Op.BRANCH
        args = [] if op is Op.JUMP else [0]
        ty = T.VOID if op is Op.JUMP else T.I1
        f.blocks[i].instructions.append(
            Instruction(op, ty, args=args, labels=[f"b{t}" for t in targets]))
    for i in (5, 6):
        f.blocks[i].instructions.append(Instruction(Op.RET, T.VOID))
    return f


class TestControlFlowGraph:
    def setup_method(self):
        self.f = diamond_with_loop()
        self.g = ControlFlowGraph.build(self.f)

    def test_reverse_postorder_covers_only_reachable(self):
        assert len(self.g.reverse_postorder) == 6
        assert self.g.unreachable == [6]

    def test_entry_is_first_in_rpo(self):
        assert self.g.reverse_postorder[0] == 0

    def test_join_is_dominated_by_the_branch_not_an_arm(self):
        idom = self.g.immediate_dominators
        assert idom[3] == 0, "both arms reach b3, so its idom is the branch"
        assert idom[4] == 3

    def test_dominance_queries(self):
        assert self.g.dominates(0, 5)
        assert not self.g.dominates(1, 3)

    def test_back_edge_found_by_dominance(self):
        assert self.g.back_edges == [(4, 3)]

    def test_natural_loop_body(self):
        assert self.g.loops() == [{3, 4}]

    def test_loop_depth(self):
        depth = self.g.loop_depth()
        assert depth[3] == 1 and depth[4] == 1 and depth[0] == 0

    def test_duplicate_successors_are_deduplicated(self):
        """`branch %c, x, x` is one edge; two would double-count in dataflow."""
        f = Function("t", T.VOID)
        f.registers[0] = T.I1
        f.blocks = [Block("a"), Block("b")]
        f.blocks[0].instructions.append(
            Instruction(Op.BRANCH, T.I1, args=[0], labels=["b", "b"]))
        f.blocks[1].instructions.append(Instruction(Op.RET, T.VOID))
        g = ControlFlowGraph.build(f)
        assert g.successors[0] == [1]
        assert g.predecessors[1] == [0]

    def test_deep_chain_does_not_overflow(self):
        """RPO uses an explicit stack; a generated if/elif ladder gets deep."""
        n = 5000
        f = Function("t", T.VOID)
        f.blocks = [Block(f"b{i}") for i in range(n)]
        for i in range(n - 1):
            f.blocks[i].instructions.append(
                Instruction(Op.JUMP, T.VOID, labels=[f"b{i+1}"]))
        f.blocks[-1].instructions.append(Instruction(Op.RET, T.VOID))
        assert len(ControlFlowGraph.build(f).reverse_postorder) == n
