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

from apc.backends.x86_64.emit import (
    MICROSOFT_X64, SYSTEM_V, X86_64Backend, _emit_parallel_moves,
)
from apc.diagnostics import DiagnosticSink
from apc.driver import Options, compile_source
from apc.ir import types as T
from apc import target as target_registry

HAS_CC = shutil.which("gcc") or shutil.which("cc")
HOST_TARGET = "x86_64-windows" if sys.platform == "win32" else "x86_64-linux"


class Recorder:
    """Just enough of `_Emitter` to capture what would be emitted."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit(self, text: str) -> None:
        self.lines.append(text)


def simulate(moves: list[tuple[str, str]]) -> dict[str, str]:
    """Run the emitted moves over symbolic register contents.

    Each register starts holding its own name, so the final contents say
    exactly which value ended up where -- which is what the argument
    convention is actually about.
    """
    e = Recorder()
    _emit_parallel_moves(e, moves)
    state: dict[str, str] = {}

    def read(operand: str) -> str:
        if operand.startswith("%"):
            return state.get(operand[1:], operand[1:])
        return operand                      # memory: unchanged by these moves

    for line in e.lines:
        assert line.startswith("movq "), line
        src, dst = line[len("movq "):].split(", ")
        state[dst[1:]] = read(src)
    return state


class TestParallelMoves:
    def test_a_move_whose_destination_is_another_source_is_ordered(self):
        """The bug: `movq %rax,%rcx` then `movq %rcx,%rdx` makes arg1 = arg0."""
        final = simulate([("rcx", "%rax"), ("rdx", "%rcx")])
        assert final["rcx"] == "rax"
        assert final["rdx"] == "rcx", "rdx must receive rcx's ORIGINAL value"

    def test_a_chain_of_four(self):
        """Exactly the shape that summed nine arguments to 30 instead of 36."""
        final = simulate([("rcx", "%rax"), ("rdx", "%rcx"),
                          ("r8", "%rdx"), ("r9", "%r8")])
        assert final == {"rcx": "rax", "rdx": "rcx", "r8": "rdx", "r9": "r8"}

    def test_a_two_cycle_is_broken_with_a_scratch(self):
        """`f(b, a)` with a and b already in each other's argument registers.
        No ordering resolves this; it needs a temporary."""
        final = simulate([("rcx", "%rdx"), ("rdx", "%rcx")])
        assert final["rcx"] == "rdx"
        assert final["rdx"] == "rcx"

    def test_a_three_cycle(self):
        final = simulate([("rcx", "%rdx"), ("rdx", "%r8"), ("r8", "%rcx")])
        # Only the three destinations are asserted: breaking the cycle also
        # leaves a value in the scratch register, which is not a result.
        assert {k: final[k] for k in ("rcx", "rdx", "r8")} == {
            "rcx": "rdx", "rdx": "r8", "r8": "rcx"}

    def test_a_cycle_with_a_tail(self):
        final = simulate([("rcx", "%rdx"), ("rdx", "%rcx"), ("r9", "%rcx")])
        assert final["rcx"] == "rdx"
        assert final["rdx"] == "rcx"
        assert final["r9"] == "rcx", "the tail reads the pre-call value too"

    def test_memory_sources_need_no_ordering(self):
        final = simulate([("rcx", "-8(%rbp)"), ("rdx", "%rcx")])
        assert final["rcx"] == "-8(%rbp)"
        assert final["rdx"] == "rcx"

    def test_a_self_move_is_not_emitted(self):
        e = Recorder()
        _emit_parallel_moves(e, [("rcx", "%rcx")])
        assert e.lines == []

    def test_every_move_is_emitted_exactly_once(self):
        moves = [("rcx", "%rax"), ("rdx", "%rcx"), ("r8", "%rsi"),
                 ("r9", "%rdi")]
        e = Recorder()
        _emit_parallel_moves(e, moves)
        assert len(e.lines) == len(moves)


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
        from apc.backend.base import BackendUnsupported
        from apc.backends.x86_64.emit import UnsupportedOperation
        assert issubclass(UnsupportedOperation, BackendUnsupported)
