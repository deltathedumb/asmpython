"""The x86-64 calling convention: the parts that fail silently.

Every bug this file guards produced a running program with a wrong number in
it. None produced a crash, and none was visible in the generated assembly
without knowing what to look for.

`_emit_parallel_moves` is tested directly rather than only through compiled
programs. Argument setup is a parallel assignment, and whether a given
program exercises the overlapping case depends on where the register
allocator happened to put things -- so a test that compiles a program tests
the allocator's mood, while these test the property.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap

import pytest

from asmpython.backends.x86_64.emit import (
    MICROSOFT_X64, SYSTEM_V, X86_64Backend, _emit_parallel_moves,
)
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir import types as T
from asmpython import target as target_registry

HAS_CC = shutil.which("gcc") or shutil.which("cc")
HOST_TARGET = "x86_64-windows" if sys.platform == "win32" else "x86_64-linux"


class Recorder:
    """Just enough of `_Emitter` to capture what would be emitted."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit(self, text: str) -> None:
        self.lines.append(text)


def simulate(moves: list[tuple[str, str]]) -> dict[str, str]:
    """Run the emitted moves over symbolic contents.

    Every location starts holding its own name, so the final contents say
    exactly which value ended up where -- which is what a calling convention
    is actually about. Both ends are operands (`%rcx` or `-8(%rbp)`), because
    the same scheduler runs for the caller (values -> argument registers) and
    for the prologue (argument registers -> values).
    """
    e = Recorder()
    _emit_parallel_moves(e, moves)
    state: dict[str, str] = {}

    for line in e.lines:
        assert line.startswith("movq "), line
        src, dst = line[len("movq "):].split(", ")
        state[dst] = state.get(src, src)
    return state


class TestParallelMoves:
    def test_a_move_whose_destination_is_another_source_is_ordered(self):
        """The bug: `movq %rax,%rcx` then `movq %rcx,%rdx` makes arg1 = arg0."""
        final = simulate([("%rcx", "%rax"), ("%rdx", "%rcx")])
        assert final["%rcx"] == "%rax"
        assert final["%rdx"] == "%rcx", "rdx must receive rcx's ORIGINAL value"

    def test_a_chain_of_four(self):
        """Exactly the shape that summed nine arguments to 30 instead of 36."""
        final = simulate([("%rcx", "%rax"), ("%rdx", "%rcx"),
                          ("%r8", "%rdx"), ("%r9", "%r8")])
        assert final == {"%rcx": "%rax", "%rdx": "%rcx", "%r8": "%rdx",
                         "%r9": "%r8"}

    def test_a_two_cycle_is_broken_with_a_scratch(self):
        """`f(b, a)` with a and b already in each other's argument registers.
        No ordering resolves this; it needs a temporary."""
        final = simulate([("%rcx", "%rdx"), ("%rdx", "%rcx")])
        assert final["%rcx"] == "%rdx"
        assert final["%rdx"] == "%rcx"

    def test_a_three_cycle(self):
        final = simulate([("%rcx", "%rdx"), ("%rdx", "%r8"), ("%r8", "%rcx")])
        # Only the three destinations are asserted: breaking the cycle also
        # leaves a value in the scratch register, which is not a result.
        assert {k: final[k] for k in ("%rcx", "%rdx", "%r8")} == {
            "%rcx": "%rdx", "%rdx": "%r8", "%r8": "%rcx"}

    def test_a_cycle_with_a_tail(self):
        final = simulate([("%rcx", "%rdx"), ("%rdx", "%rcx"), ("%r9", "%rcx")])
        assert final["%rcx"] == "%rdx"
        assert final["%rdx"] == "%rcx"
        assert final["%r9"] == "%rcx", "the tail reads the pre-call value too"

    def test_memory_sources_need_no_ordering(self):
        final = simulate([("%rcx", "-8(%rbp)"), ("%rdx", "%rcx")])
        assert final["%rcx"] == "-8(%rbp)"
        assert final["%rdx"] == "%rcx"

    def test_a_self_move_is_not_emitted(self):
        e = Recorder()
        _emit_parallel_moves(e, [("%rcx", "%rcx")])
        assert e.lines == []

    def test_every_move_is_emitted_exactly_once(self):
        moves = [("%rcx", "%rax"), ("%rdx", "%rcx"), ("%r8", "%rsi"),
                 ("%r9", "%rdi")]
        e = Recorder()
        _emit_parallel_moves(e, moves)
        assert len(e.lines) == len(moves)


class TestThePrologueIsAParallelAssignmentToo:
    """The caller's mirror image, and it had the identical bug.

    Argument registers arrive holding values that must be moved to wherever
    the allocator put them -- and one parameter's destination is often another
    parameter's arrival register. Emitting those in parameter order produced

        movq %rcx, %r8      p0 (arrived in rcx) -> its register r8
        movq %r8, %rcx      p2 arrived in r8, which the line above destroyed

    so p2 arrived holding p0. A six-argument call returned -785 instead of
    147, and adding a print statement made it go away.
    """

    def test_a_destination_that_is_another_arrival_is_ordered(self):
        final = simulate([("%r8", "%rcx"), ("%rcx", "%r8")])
        assert final["%r8"] == "%rcx"
        assert final["%rcx"] == "%r8"

    def test_moving_arrivals_into_slots(self):
        final = simulate([("-8(%rbp)", "%rcx"), ("%rcx", "%rdx")])
        assert final["-8(%rbp)"] == "%rcx", "the slot must get rcx's arrival"
        assert final["%rcx"] == "%rdx"

    def test_a_stacked_argument_loaded_into_a_live_arrival_register(self):
        """The stacked parameter's source is memory and cannot be clobbered,
        but its DESTINATION can be another parameter's arrival register."""
        final = simulate([("%rcx", "48(%rbp)"), ("-8(%rbp)", "%rcx")])
        assert final["-8(%rbp)"] == "%rcx"
        assert final["%rcx"] == "48(%rbp)"

    def test_memory_to_memory_is_staged(self):
        e = Recorder()
        _emit_parallel_moves(e, [("-8(%rbp)", "48(%rbp)")])
        assert len(e.lines) == 2, "a memory-to-memory move needs a register"
        assert all(line.startswith("movq ") for line in e.lines)


class TestHiddenCalls:
    """A call the IR does not show still destroys caller-saved registers."""

    def test_float_remainder_counts_as_a_call(self):
        """x86-64 has no float remainder instruction and emits `call fmod`.
        The shared liveness saw an arithmetic opcode, so a value live across
        it went into rax and `float(i0)` afterwards read fmod's leftovers."""
        from asmpython.backends.x86_64.emit import _clobbers_volatiles
        from asmpython.ir.module import Instruction
        from asmpython.ir.opcodes import Op

        assert _clobbers_volatiles(
            Instruction(Op.REM, T.F64, dst=0, args=[1, 2]))
        assert not _clobbers_volatiles(
            Instruction(Op.REM, T.I64, dst=0, args=[1, 2]))
        assert _clobbers_volatiles(
            Instruction(Op.CALL, T.I64, dst=0, args=[], sym="f"))
        assert not _clobbers_volatiles(
            Instruction(Op.ADD, T.F64, dst=0, args=[1, 2]))

    def test_liveness_honours_the_predicate(self):
        """`live_across_calls` must widen when a backend says so."""
        from asmpython.backend import Liveness
        from asmpython.ir import Builder, Function, Module, verify
        from asmpython.ir.module import Instruction
        from asmpython.ir.opcodes import Op

        m = Module("t")
        f = Function("main", T.I64)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        kept = b.const(T.I64, 42)              # live across the REM below
        x, y = b.const(T.F64, 7.5), b.const(T.F64, 2.0)
        r = b.reg(T.F64)
        b.emit(Instruction(Op.REM, T.F64, dst=r, args=[x, y]))
        b.ret(b.add(T.I64, kept, b.const(T.I64, 1)))
        verify(m)

        from asmpython.backends.x86_64.emit import _clobbers_volatiles
        live = Liveness.compute(f)
        assert kept not in live.live_across_calls(), \
            "the IR alone shows no call here"
        assert kept in live.live_across_calls(_clobbers_volatiles), \
            "the backend's fmod call must make it live across a call"

    def test_verify_allocation_checks_the_caller_saved_rule(self):
        """It was documented and not implemented, which is how the bug got
        past a check that claimed to cover it."""
        from asmpython.backend import (
            InRegister, Liveness, RegisterFile, allocate, verify_allocation,
        )
        from asmpython.ir import Builder, Function, Module, verify

        m = Module("t")
        callee = Function("g", T.I64)
        m.functions.append(callee)
        cb = Builder(callee)
        cb.switch_to(cb.new_block("entry"))
        cb.ret(cb.const(T.I64, 1))

        f = Function("main", T.I64)
        m.functions.append(f)
        b = Builder(f)
        b.switch_to(b.new_block("entry"))
        kept = b.const(T.I64, 42)
        got = b.call(T.I64, "g", [])
        b.ret(b.add(T.I64, kept, got))
        verify(m)

        rf = RegisterFile(general=("v0", "v1", "s0"),
                          callee_saved=frozenset({"s0"}))
        alloc = allocate(f, rf)
        assert verify_allocation(f, alloc, file=rf) == []

        # Force the violation the checker must now see.
        for reg in Liveness.compute(f).live_across_calls():
            alloc.locations[reg] = InRegister("v0")
        problems = verify_allocation(f, alloc, file=rf)
        assert problems, "a value live across a call in a volatile went unseen"
        assert any("destroy" in p for p in problems), (
            "the conflict was reported but the caller-saved rule was not:\n"
            + "\n".join(problems))


class TestArgumentPlacement:
    def places(self, types, abi):
        return X86_64Backend._argument_places(types, abi)

    def test_system_v_indexes_the_two_sequences_independently(self):
        places = self.places([T.I64, T.F64, T.I64, T.F64], SYSTEM_V)
        assert [p.register for p in places] == ["rdi", "xmm0", "rsi", "xmm1"]

    def test_microsoft_x64_indexes_both_by_position(self):
        """The same call places the float in xmm1, not xmm0: the integer
        ahead of it consumes the slot. Getting this wrong reads an argument
        the caller never wrote."""
        places = self.places([T.I64, T.F64, T.I64, T.F64], MICROSOFT_X64)
        assert [p.register for p in places] == ["rcx", "xmm1", "r8", "xmm3"]

    def test_overflow_goes_to_the_stack_in_order(self):
        places = self.places([T.I64] * 8, SYSTEM_V)
        assert [p.register for p in places[:6]] == list(
            SYSTEM_V.argument_registers)
        assert [p.stack_offset for p in places[6:]] == [0, 8]

    def test_stacked_arguments_sit_above_the_shadow_space(self):
        """Microsoft x64 reserves 32 bytes the callee owns; the fifth argument
        starts after it, not at rsp."""
        places = self.places([T.I64] * 6, MICROSOFT_X64)
        assert [p.stack_offset for p in places[4:]] == [32, 40]

    def test_a_float_can_overflow_to_the_stack_too(self):
        places = self.places([T.F64] * 10, SYSTEM_V)
        assert places[8].on_stack and places[8].is_float


@pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
class TestCompiledCallsAgree:
    """Compile it, run it, compare with CPython."""

    def run_program(self, src: str, tmp_path, backend: str):
        path = tmp_path / f"{backend.replace('-', '_')}.py"
        path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
        exe = tmp_path / f"{backend.replace('-', '_')}.exe"
        sink = DiagnosticSink()
        result = compile_source(Options(
            source=path, output=exe, backend=backend, link=True,
            target=target_registry.get(
                HOST_TARGET if backend == "x86-64" else "c"),
            workdir=tmp_path / f"w{backend}"), sink)
        assert result.ok, [d.message for d in sink.diagnostics]
        proc = subprocess.run([str(result.program)], capture_output=True,
                              text=True)
        return proc.stdout.split("\n")[:-1]

    def cpython(self, src: str) -> list[str]:
        out: list[str] = []
        ns = {"print": lambda *a: out.append(
            " ".join(f"{x:f}" if isinstance(x, float) else str(x) for x in a))}
        exec(compile(textwrap.dedent(src).strip() + "\n", "<t>", "exec"), ns)
        ns["main"]()
        return out

    MANY_ARGS = """
        def wide(a0: int, a1: int, a2: int, a3: int, a4: int,
                 a5: int, a6: int, a7: int, a8: int) -> int:
            return a0 + a1 + a2 + a3 + a4 + a5 + a6 + a7 + a8

        def main() -> int:
            print(wide(1, 2, 3, 4, 5, 6, 7, 8, 9))
            print(wide(0, 0, 0, 0, 0, 0, 0, 0, 100))
            return 0
    """

    MIXED = """
        def mix(a: int, b: float, c: int, d: float, e: int, f: float) -> float:
            return float(a) + b + float(c) + d + float(e) + f

        def main() -> int:
            print(mix(1, 2.5, 3, 4.25, 5, 6.125))
            return 0
    """

    REVERSED = """
        def sub(a: int, b: int) -> int:
            return a - b

        def main() -> int:
            x: int = 10
            y: int = 3
            print(sub(x, y))
            print(sub(y, x))
            return 0
    """

    @pytest.mark.parametrize("name", ["MANY_ARGS", "MIXED", "REVERSED"])
    @pytest.mark.parametrize("backend", ["c", "x86-64"])
    def test_matches_cpython(self, name, backend, tmp_path):
        src = getattr(self, name)
        assert self.run_program(src, tmp_path, backend) == self.cpython(src)

    def test_more_arguments_than_registers_no_longer_refuses(self, tmp_path):
        """It used to raise; now it stacks them."""
        assert self.run_program(self.MANY_ARGS, tmp_path, "x86-64") == ["45",
                                                                       "100"]


class TestBackendLimitsAreDiagnostics:
    def test_an_unsupported_operation_is_reported_not_raised(self, tmp_path):
        """A backend limitation reaching the user as a traceback reads as
        "you found a compiler bug" when it means "use --backend c"."""
        from asmpython.backend.base import BackendUnsupported
        from asmpython.backends.x86_64.emit import UnsupportedOperation
        assert issubclass(UnsupportedOperation, BackendUnsupported)
