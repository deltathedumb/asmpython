"""The machine subset: widths, memory intrinsics, and what they refuse.

STAGE 1 OF `docs/INERT-RUNTIME.md`. The goal that document argues for is a
backend that does not have to define the object runtime -- 229 `apy_*` symbols
today, of which exactly one backend has any. The route it chooses is to write
the runtime in the IR rather than in C, and the place it gets written is the
Python frontend's STATIC path, because that path emits no `apy_*` at all: a
runtime written in it stands on the machine rather than on itself.

That needs two things the static path did not have. Machine WIDTHS, so a value
can be laid out where something else will read the same bytes; and MEMORY, so
there is somewhere to lay it. This file is the proof that both are there.

WHAT IS BEING PROVED, AND HOW CHEAPLY. Everything here goes through the IR
verifier and the IR INTERPRETER -- no C compiler, no backend, no linker -- so
the whole file runs in under a second. That is deliberate: the interpreter
executes the same IR a backend consumes, so it answers "did this compile to the
right instructions" without paying for a toolchain. The backend end of it is
stage 2's problem and is proved by different means.

WHY THE REFUSALS GET AS MUCH ROOM AS THE ACCEPTANCES. A width exists because
something else reads the same bytes. So the failure that matters is not a
program that gets rejected -- it is one that silently truncates, sign-extends
or compares as the wrong signedness and produces a plausible answer. Every one
of those has a test below, and each has its OWN diagnostic code, because a
runtime is exactly the kind of code where a wrong answer looks like a wrong
answer somewhere else entirely.
"""
from __future__ import annotations

import textwrap
from io import StringIO

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter
from asmpython.ir.verifier import verify


def compile_text(src: str, tmp_path):
    path = tmp_path / "prog.py"
    path.write_text(textwrap.dedent(src).strip() + "\n", encoding="utf-8")
    sink = DiagnosticSink()
    return compile_source(Options(source=path), sink), sink


def run_text(src: str, tmp_path) -> tuple[list[str], int]:
    """Compile, VERIFY, and run in the IR interpreter.

    The verify call is not redundant with the driver's own: this asserts the
    IR is well-formed before the interpreter is asked to execute it, so a
    frontend that emits `i64.store` into a `u8` slot fails HERE, naming the
    instruction, rather than as a strange value several prints later.
    """
    result, sink = compile_text(src, tmp_path)
    assert result.ok, [f"{d.code}: {d.message}" for d in sink.diagnostics]
    verify(result.module)
    out = StringIO()
    value = Interpreter(result.module, out=out).run("main")
    text = out.getvalue()
    return (text.split("\n")[:-1] if text else []), value


def codes(sink) -> list[str]:
    return [d.code for d in sink.diagnostics]


def refused(src: str, tmp_path) -> list[str]:
    """The diagnostic codes a bad program produces. Never an exception: the
    contract analysis has with lowering is that lowering sees only what
    analysis accepted, and a crash here means that contract broke."""
    result, sink = compile_text(src, tmp_path)
    assert not result.ok, "expected a diagnostic, got a compiled module"
    return codes(sink)


class TestTheTypesExist:
    """A width is spellable, and it means the IR type of the same name."""

    def test_a_function_may_be_annotated_with_a_width(self, tmp_path):
        lines, _ = run_text("""
            def half(n: i32) -> i32:
                return n // 2

            def main() -> int:
                print(half(i32(9)))
                return 0
        """, tmp_path)
        assert lines == ["4"]

    def test_a_width_annotation_keeps_the_function_on_the_static_path(
            self, tmp_path):
        """The DECIDING PROPERTY of the whole exercise.

        A function whose annotations this frontend does not know goes dynamic,
        and a dynamic function calls the object runtime for every operation --
        which is the runtime this work exists to stop needing. So a function
        written in machine types must emit no `apy_*` at all, and that is
        asserted against the IR rather than inferred from it compiling.
        """
        result, sink = compile_text("""
            def add(a: u32, b: u32) -> u32:
                return a + b

            def main() -> int:
                print(add(u32(2), u32(3)))
                return 0
        """, tmp_path)
        assert result.ok, [d.message for d in sink.diagnostics]
        symbols = {ins.sym for fn in result.module.functions
                   for b in fn.blocks for ins in b.instructions
                   if ins.sym}
        assert not [s for s in symbols if s.startswith("apy_")], symbols

    def test_every_width_round_trips_through_memory(self, tmp_path):
        """One cell, eleven types, read back. The cross-product in miniature.

        `u64` is stored at its maximum because that is the value that
        distinguishes an unsigned load from a signed one: read as i64 it is
        -1, and every comparison against it flips.
        """
        lines, _ = run_text("""
            def main() -> int:
                p: ptr = alloca(64)
                store(i8, -128, p)
                store(u8, 255, offset(p, 1))
                store(i16, -32768, offset(p, 2))
                store(u16, 65535, offset(p, 4))
                store(i32, -2147483648, offset(p, 8))
                store(u32, 4294967295, offset(p, 16))
                store(i64, -9223372036854775808, offset(p, 24))
                store(u64, 18446744073709551615, offset(p, 32))
                store(f32, 0.5, offset(p, 40))
                store(f64, 0.125, offset(p, 48))
                print(load(i8, p))
                print(load(u8, offset(p, 1)))
                print(load(i16, offset(p, 2)))
                print(load(u16, offset(p, 4)))
                print(load(i32, offset(p, 8)))
                print(load(u32, offset(p, 16)))
                print(load(i64, offset(p, 24)))
                print(load(u64, offset(p, 32)) == u64(18446744073709551615))
                print(load(f32, offset(p, 40)))
                print(load(f64, offset(p, 48)))
                return 0
        """, tmp_path)
        assert lines == ["-128", "255", "-32768", "65535", "-2147483648",
                         "4294967295", "-9223372036854775808", "True",
                         "0.5", "0.125"]


class TestMemory:
    """`alloca`, `load`, `store`, `offset`, `sizeof`."""

    def test_the_tagged_cell(self, tmp_path):
        """THE CASE `docs/INERT-RUNTIME.md` NAMES as stage 1's proof: allocate,
        store a kind and a payload, read both back. It is the shape every
        object in the ported runtime will have."""
        lines, _ = run_text("""
            def cell(kind: i64, payload: i64) -> i64:
                p: ptr = alloca(16)
                store(i64, kind, p)
                store(i64, payload, offset(p, sizeof(i64)))
                return load(i64, p) * 1000 + load(i64, offset(p, 8))

            def main() -> int:
                print(cell(7, 1234))
                return 0
        """, tmp_path)
        assert lines == ["8234"]

    def test_sizeof_is_a_compile_time_constant(self, tmp_path):
        """It must FOLD, not call.

        A struct layout written as `offset(p, sizeof(i64) * 2)` has to cost
        what writing `16` costs, and it has to be checkable by reading the IR
        -- a runtime that computes its own field offsets at run time is one
        nobody can reason about from the output.
        """
        result, sink = compile_text("""
            def main() -> int:
                print(sizeof(i8) + sizeof(u16) + sizeof(i32) + sizeof(f64)
                      + sizeof(ptr))
                return 0
        """, tmp_path)
        assert result.ok, [d.message for d in sink.diagnostics]
        assert not [ins for fn in result.module.functions
                    for b in fn.blocks for ins in b.instructions
                    if ins.sym and ins.sym.startswith("sizeof")]
        out = StringIO()
        Interpreter(result.module, out=out).run("main")
        assert out.getvalue() == "23\n"          # 1 + 2 + 4 + 8 + 8

    def test_a_layout_may_be_written_in_terms_of_sizeof(self, tmp_path):
        """`alloca` needs its size before the program runs, so an arithmetic
        expression over `sizeof` has to FOLD -- otherwise a struct size has to
        be written as a number with a comment saying where it came from, which
        is how a layout and the code that reads it stop agreeing.

        The four-slot array below is 8 + 8 + 32 = 48 bytes, and the last slot
        is written and read at the offset the same arithmetic produces.
        """
        lines, _ = run_text("""
            def main() -> int:
                p: ptr = alloca(sizeof(i64) + sizeof(ptr) + sizeof(i64) * 4)
                store(i64, 5, offset(p, sizeof(i64) + sizeof(ptr)
                                        + sizeof(i64) * 3))
                print(load(i64, offset(p, 40)))
                return 0
        """, tmp_path)
        assert lines == ["5"]

    def test_sizeof_adapts_to_a_machine_typed_index(self, tmp_path):
        """`i * sizeof(i32)` for an `i64` index. `sizeof` yields a Python
        `int`, and without adapting like the literal it is, indexing an array
        with a machine-typed counter needed a conversion round every step."""
        lines, _ = run_text("""
            def main() -> int:
                p: ptr = alloca(16)
                store(i32, 77, offset(p, 8))
                i: i64 = 2
                print(load(i32, offset(p, i * sizeof(i32))))
                return 0
        """, tmp_path)
        assert lines == ["77"]

    def test_offset_takes_bytes_not_elements(self, tmp_path):
        """There is no element type in this IR, so an index is multiplied
        where it is written. Asserted because the alternative -- an `offset`
        that scaled -- would be silently wrong for every type but one."""
        lines, _ = run_text("""
            def main() -> int:
                p: ptr = alloca(16)
                store(i32, 11, offset(p, 0))
                store(i32, 22, offset(p, 4))
                store(i32, 33, offset(p, 2 * 4))
                i: i64 = 1
                print(load(i32, offset(p, i * sizeof(i32))))
                print(load(i32, offset(p, 8)))
                return 0
        """, tmp_path)
        assert lines == ["22", "33"]

    def test_a_pointer_survives_being_passed_and_returned(self, tmp_path):
        """The property an allocator needs: a `ptr` is an ordinary value.

        `alloca` storage does NOT survive its own function returning, so the
        pointer that crosses a boundary here is one the caller owns -- which
        is the shape a real allocator has, and the shape this had to be
        checked in.
        """
        lines, _ = run_text("""
            def fill(p: ptr, n: i64) -> ptr:
                store(i64, n, p)
                return p

            def main() -> int:
                room: ptr = alloca(8)
                print(load(i64, fill(room, 99)))
                return 0
        """, tmp_path)
        assert lines == ["99"]

    def test_a_pointer_is_truthy_when_it_is_not_null(self, tmp_path):
        """`if p:` is how every allocation check in a runtime is written."""
        lines, _ = run_text("""
            def main() -> int:
                p: ptr = alloca(8)
                null: ptr = ptr(0)
                if p:
                    print(1)
                if not null:
                    print(2)
                print(p == null)
                return 0
        """, tmp_path)
        assert lines == ["1", "2", "False"]

    def test_a_pointer_converts_to_an_integer_and_back(self, tmp_path):
        """An address arrives from an allocator as a number. Round-tripping it
        must not disturb it -- the conversion is a reinterpretation of 64 bits
        and nothing else."""
        lines, _ = run_text("""
            def main() -> int:
                p: ptr = alloca(8)
                store(i64, 42, p)
                address: u64 = u64(p)
                back: ptr = ptr(address)
                print(load(i64, back))
                return 0
        """, tmp_path)
        assert lines == ["42"]


class TestWidthsBehaveLikeWidths:
    """Wrapping, signedness, and division -- the places a width is not an int."""

    def test_unsigned_arithmetic_wraps_at_its_own_width(self, tmp_path):
        lines, _ = run_text("""
            def main() -> int:
                a: u8 = 250
                b: u16 = 65530
                print(a + 10)
                print(b + 10)
                return 0
        """, tmp_path)
        assert lines == ["4", "4"]

    def test_signed_comparison_and_unsigned_comparison_differ(self, tmp_path):
        """THE BUG THIS PREVENTS, stated as a test.

        The same 64 bits are a huge number as `u64` and -1 as `i64`. Before
        the frontend compared at the operand's own width it compared
        everything as a signed i64, so every address above 2^63 sorted below
        zero -- and the coercion that got it there was a same-width truncation
        the verifier had no reason to object to.
        """
        lines, _ = run_text("""
            def main() -> int:
                big: u64 = 18446744073709551615
                small: i64 = -1
                print(big > u64(0))
                print(small > 0)
                return 0
        """, tmp_path)
        assert lines == ["True", "False"]

    def test_floor_division_still_floors_at_every_width(self, tmp_path):
        """Python's `//` rounds toward negative infinity; the machine's DIV
        truncates toward zero. The correction has to survive being applied at
        a width that is not 64 bits."""
        lines, _ = run_text("""
            def main() -> int:
                a: i32 = -7
                b: i16 = -7
                print(a // 2)
                print(b % 3)
                return 0
        """, tmp_path)
        assert lines == ["-4", "2"]

    def test_a_narrow_float_divides_and_prints_as_a_float(self, tmp_path):
        """`f32` is a float. Choosing the writer on `ty is FLOAT` alone sent
        it through the integer path, which does not print a wrong float -- it
        prints an integer, because the coercion truncates first."""
        lines, _ = run_text("""
            def main() -> int:
                f: f32 = 2.5
                print(f / f32(2.0))
                return 0
        """, tmp_path)
        assert lines == ["1.25"]

    def test_a_literal_adapts_to_the_width_beside_it(self, tmp_path):
        """`n + 1` must not have to be written `n + u32(1)`.

        A literal has no width of its own to lose, so it takes the other
        side's. Everything that DOES have a width keeps it -- that asymmetry
        is the rule, and it is what makes `_machine_binop` enforceable.
        """
        lines, _ = run_text("""
            def main() -> int:
                n: u32 = 4294967290
                n = n + 5
                print(n)
                print(n > 4294967000)
                print(-1 + i16(1))
                return 0
        """, tmp_path)
        assert lines == ["4294967295", "True", "0"]


class TestTheRefusals:
    """Each has its own code, because each has its own fix."""

    def test_two_widths_do_not_add(self, tmp_path):
        assert "E0013" in refused("""
            def f(a: i32, b: i64) -> i64:
                return a + b

            def main() -> int:
                return 0
        """, tmp_path)

    def test_two_widths_do_not_compare(self, tmp_path):
        """The same rule and the SAME CODE as addition. Comparing a signed
        width against an unsigned one has no answer that is right for both,
        which is the same fact as not being able to add them."""
        assert "E0013" in refused("""
            def f(a: u32, b: i32) -> bool:
                return a < b

            def main() -> int:
                return 0
        """, tmp_path)

    def test_a_literal_that_does_not_fit_is_refused_not_wrapped(self, tmp_path):
        """`x: u8 = 300` silently becoming 44 is the exact failure that makes
        a width annotation worse than no annotation at all."""
        assert "E0012" in refused("""
            def main() -> int:
                x: u8 = 300
                print(x)
                return 0
        """, tmp_path)

    def test_true_division_of_an_integer_width_is_refused(self, tmp_path):
        """Python's `/` is float-valued, so `a / b` on two i32s would have to
        produce a float and lose the width the author asked for."""
        assert "E0014" in refused("""
            def f(a: i32, b: i32) -> i32:
                return a / b

            def main() -> int:
                return 0
        """, tmp_path)

    def test_power_is_refused_rather_than_guessed(self, tmp_path):
        """`**` expands to multiplications on `int` and calls a runtime on
        `float`. Neither answer is obviously right for a fixed width, and
        nothing that needs a width needs `**`."""
        assert "E0015" in refused("""
            def f(a: i32) -> i32:
                return a ** 2

            def main() -> int:
                return 0
        """, tmp_path)

    def test_a_ternary_over_two_types_is_refused(self, tmp_path):
        """Without this, the whole expression was typed `int` and the i32 arm
        was silently widened into a register of the wrong width -- reported by
        the verifier against a register rather than against this line."""
        assert "E0016" in refused("""
            def f(a: i32, c: bool) -> i32:
                return a if c else 0.5

            def main() -> int:
                return 0
        """, tmp_path)

    def test_the_first_argument_to_load_must_be_a_type(self, tmp_path):
        assert "E0017" in refused("""
            def f(p: ptr) -> i64:
                return load(8, p)

            def main() -> int:
                return 0
        """, tmp_path)

    def test_alloca_needs_a_size_it_can_know(self, tmp_path):
        """Frame storage is laid out before the function runs -- `Op.ALLOCA`
        carries its size in an immediate and a backend has nowhere to put a
        size it only learns later. A runtime size is the allocator's job."""
        assert "E0018" in refused("""
            def f(n: i64) -> ptr:
                return alloca(n)

            def main() -> int:
                return 0
        """, tmp_path)
        assert "E0018" in refused("""
            def f() -> ptr:
                return alloca(0)

            def main() -> int:
                return 0
        """, tmp_path)

    def test_an_address_must_be_a_pointer(self, tmp_path):
        """An integer is not an address until someone says it is. Otherwise
        `load(i64, n)` on a loop counter compiles."""
        assert "E0019" in refused("""
            def f(n: i64) -> i64:
                return load(i64, n)

            def main() -> int:
                return 0
        """, tmp_path)

    def test_a_pointer_comes_from_an_integer_and_nothing_else(self, tmp_path):
        assert "E0033" in refused("""
            def f(x: f64) -> ptr:
                return ptr(x)

            def main() -> int:
                return 0
        """, tmp_path)

    def test_a_pointer_does_not_narrow(self, tmp_path):
        """Narrowing an address to 32 bits is a real thing to want on a 32-bit
        target and a silent disaster on this one, so it has to be two steps."""
        assert "E0034" in refused("""
            def f(p: ptr) -> i32:
                return i32(p)

            def main() -> int:
                return 0
        """, tmp_path)

    def test_a_wrong_width_is_a_type_error_like_any_other(self, tmp_path):
        assert "E0060" in refused("""
            def f(a: i32) -> i64:
                return a

            def main() -> int:
                return 0
        """, tmp_path)

    def test_an_intrinsic_with_the_wrong_arity_is_reported(self, tmp_path):
        assert "E0054" in refused("""
            def f(p: ptr) -> i64:
                return load(i64, p, 1)

            def main() -> int:
                return 0
        """, tmp_path)


class TestTheNamesAreNotReserved:
    """A program of its own may still call something `load`.

    The intrinsics are looked up AFTER the module's own functions, exactly as
    `int` is -- so adding eleven type names and five intrinsics to the
    frontend's vocabulary takes nothing away from a program that was already
    compiling.
    """

    def test_a_user_function_named_load_wins(self, tmp_path):
        lines, _ = run_text("""
            def load(n: int) -> int:
                return n + 1

            def offset(n: int) -> int:
                return n * 2

            def main() -> int:
                print(load(1), offset(3))
                return 0
        """, tmp_path)
        assert lines == ["2 6"]

    def test_a_user_function_named_after_a_width_wins(self, tmp_path):
        lines, _ = run_text("""
            def i32(n: int) -> int:
                return n - 1

            def main() -> int:
                print(i32(10))
                return 0
        """, tmp_path)
        assert lines == ["9"]
