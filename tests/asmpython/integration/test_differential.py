"""Randomly generated programs, run four ways and compared.

Every hand-written test asserts something someone thought of. The bugs that
survive are the ones nobody thought of: the parallel-move collapse needed four
arguments AND an allocator that happened to place one in another's
destination; the float `%` sign error needed a negative dividend; the
descending-range bug needed a literal negative step. Each was found by
accident, and each was a wrong answer in a program that ran.

So this generates programs from the grammar the frontend accepts and checks
that CPython, the reference interpreter, the C backend and the x86-64 backend
all agree. Seeded, so a failure reproduces exactly: the seed is the test id.

It works. On its first run it found `0.0 // -9.2` returning +0.0 where Python
gives -0.0, then `0.0 % -6.2` with the same sign lost, then float `**`
disagreeing with CPython in the last bit.

WHAT THE GENERATOR MUST NOT PRODUCE matters as much as what it does. The
subset is 64-bit where Python is arbitrary-precision, so an expression that
overflows is a documented divergence, not a bug -- and a suite that reports
one wastes the reader's attention on something already known. Overflow is
made unbuildable rather than unlikely: see `Bounded`.
"""
from __future__ import annotations

import random
import shutil
import subprocess
import sys
from io import StringIO
from pathlib import Path

import pytest

from asmpython import target as target_registry
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter

HAS_CC = shutil.which("gcc") or shutil.which("cc")
HOST_TARGET = "x86_64-windows" if sys.platform == "win32" else "x86_64-linux"

#: Every generated expression carries a conservative ceiling on its magnitude,
#: and no node is built whose ceiling would exceed this.
#:
#: Bounding the LEAVES is not enough, which is the whole point: a product of
#: two bounded values is not bounded by either. With leaves under 1000 and
#: exponents up to 3, seed 661 produced `(i0 - (i0 << 5)) * -i0` = 1.7e19,
#: which wraps at 64 bits and does not in CPython -- and the disagreement took
#: a while to attribute to the test rather than to the compiler.
INT_LIMIT = 2 ** 62
FLOAT_LIMIT = 1e100
INT_RANGE = (-1000, 1000)


class Bounded:
    """An expression, and a ceiling on the magnitude it can evaluate to."""

    __slots__ = ("src", "bound")

    def __init__(self, src: str, bound: float) -> None:
        self.src = src
        self.bound = bound

    def __str__(self) -> str:
        return self.src


class ProgramGenerator:
    """Builds one random program from the accepted grammar.

    Every expression method returns a `Bounded`. A combinator computes the
    result's ceiling from its operands' and falls back to a leaf when that
    ceiling would exceed the limit, so an overflowing intermediate cannot be
    constructed at all.
    """

    def __init__(self, seed: int) -> None:
        self.rng = random.Random(seed)
        #: name -> ceiling, so a variable's magnitude is known where it is used
        self.int_vars: dict[str, float] = {}
        self.float_vars: dict[str, float] = {}

    # -- leaves --------------------------------------------------------------
    def int_literal(self) -> Bounded:
        v = self.rng.randint(*INT_RANGE)
        return Bounded(str(v) if v >= 0 else f"({v})", abs(v))

    def float_literal(self) -> Bounded:
        v = self.rng.uniform(-100, 100)
        return Bounded(f"{v:.4f}" if v >= 0 else f"({v:.4f})", abs(v) or 1.0)

    def int_leaf(self) -> Bounded:
        if self.int_vars and self.rng.random() < 0.5:
            name = self.rng.choice(list(self.int_vars))
            return Bounded(name, self.int_vars[name])
        return self.int_literal()

    def float_leaf(self) -> Bounded:
        if self.float_vars and self.rng.random() < 0.5:
            name = self.rng.choice(list(self.float_vars))
            return Bounded(name, self.float_vars[name])
        return self.float_literal()

    def nonzero(self) -> Bounded:
        """A non-zero divisor.

        Zero traps in a compiled program and raises in CPython. That is a
        divergence, not a disagreement, so it is generated deliberately in a
        hand-written test rather than accidentally here.
        """
        v = 0
        while v == 0:
            v = self.rng.randint(-50, 50)
        return Bounded(str(v) if v > 0 else f"({v})", abs(v))

    def nonzero_float(self) -> Bounded:
        v = 0.0
        while abs(v) < 0.5:
            v = self.rng.uniform(-20, 20)
        return Bounded(f"{v:.4f}" if v > 0 else f"({v:.4f})", abs(v))

    # -- expressions ---------------------------------------------------------
    def int_expr(self, depth: int = 0) -> Bounded:
        r = self.rng
        if depth >= 3:
            return self.int_leaf()
        choice = r.random()

        if choice < 0.14:
            return self.int_leaf()
        if choice < 0.24:
            n = r.randint(0, 3)
            base = self.int_leaf()
            bound = base.bound ** n if n else 1
            if bound <= INT_LIMIT:
                return Bounded(f"({base} ** {n})", max(bound, 1))
            return base
        if choice < 0.32:
            a = self.int_expr(depth + 1)
            return Bounded(f"(-{a})", a.bound)
        if choice < 0.44:
            # Both only shrink: |a // b| <= |a|, and |a % b| < |b|.
            a, b = self.int_expr(depth + 1), self.nonzero()
            op = r.choice(["//", "%"])
            return Bounded(f"({a} {op} {b})",
                           a.bound if op == "//" else b.bound)
        if choice < 0.54:
            a = self.int_leaf()
            if r.random() < 0.5:
                n = r.randint(0, 8)
                bound = a.bound * (2 ** n)
                if bound <= INT_LIMIT:
                    return Bounded(f"({a} << {n})", bound)
                return a
            return Bounded(f"({a} >> {r.randint(0, 20)})", a.bound)
        if choice < 0.64:
            a, b = self.int_expr(depth + 1), self.int_expr(depth + 1)
            op = r.choice(["&", "|", "^"])
            # Really bounded by the larger operand rounded up to a power of
            # two; the sum is a simpler over-estimate and over is the safe way
            # to be wrong.
            return Bounded(f"({a} {op} {b})", a.bound + b.bound)
        if choice < 0.72:
            f = self.float_expr(depth + 1)
            if f.bound < INT_LIMIT:
                return Bounded(f"int({f})", f.bound)
            return self.int_leaf()
        if choice < 0.80:
            return Bounded(f"int({self.bool_expr(depth + 1)})", 1)

        a, b = self.int_expr(depth + 1), self.int_expr(depth + 1)
        op = r.choice(["+", "-", "*"])
        bound = a.bound * b.bound if op == "*" else a.bound + b.bound
        if bound > INT_LIMIT:
            return a
        return Bounded(f"({a} {op} {b})", bound)

    def float_expr(self, depth: int = 0) -> Bounded:
        r = self.rng
        if depth >= 3:
            return self.float_leaf()
        choice = r.random()

        if choice < 0.22:
            return self.float_leaf()
        if choice < 0.32:
            a = self.float_expr(depth + 1)
            return Bounded(f"(-{a})", a.bound)
        if choice < 0.42:
            a = self.int_leaf()
            return Bounded(f"float({a})", max(a.bound, 1.0))
        if choice < 0.56:
            a, b = self.float_expr(depth + 1), self.nonzero_float()
            op = r.choice(["//", "%", "/"])
            if op == "%":
                return Bounded(f"({a} {op} {b})", b.bound)
            # The divisor can be as small as 0.5, which doubles the magnitude.
            bound = a.bound / min(b.bound, 1.0)
            if bound > FLOAT_LIMIT:
                return a
            return Bounded(f"({a} {op} {b})", bound)
        if choice < 0.66:
            n = r.randint(0, 3)
            base = self.float_leaf()
            bound = base.bound ** n if n else 1.0
            if bound <= FLOAT_LIMIT:
                return Bounded(f"({base} ** {n})", max(bound, 1.0))
            return base

        a, b = self.float_expr(depth + 1), self.float_expr(depth + 1)
        op = r.choice(["+", "-", "*"])
        bound = a.bound * b.bound if op == "*" else a.bound + b.bound
        if bound > FLOAT_LIMIT:
            return a
        return Bounded(f"({a} {op} {b})", bound)

    def bool_expr(self, depth: int = 0) -> Bounded:
        r = self.rng
        cmp_ops = ["<", "<=", ">", ">=", "==", "!="]
        if depth >= 2:
            op = r.choice(cmp_ops)
            return Bounded(f"({self.int_leaf()} {op} {self.int_leaf()})", 1)
        choice = r.random()
        if choice < 0.25:
            op = r.choice(cmp_ops)
            return Bounded(f"({self.int_expr(depth + 1)} {op} "
                           f"{self.int_expr(depth + 1)})", 1)
        if choice < 0.45:
            op = r.choice(cmp_ops)
            return Bounded(f"({self.float_expr(depth + 1)} {op} "
                           f"{self.float_expr(depth + 1)})", 1)
        if choice < 0.60:
            return Bounded(f"(not {self.bool_expr(depth + 1)})", 1)
        if choice < 0.75:
            # Chained: the middle operand must be evaluated exactly once.
            a, b, c = self.int_leaf(), self.int_leaf(), self.int_leaf()
            return Bounded(f"({a} < {b} < {c})", 1)
        op = r.choice(["and", "or"])
        return Bounded(f"({self.bool_expr(depth + 1)} {op} "
                       f"{self.bool_expr(depth + 1)})", 1)

    # -- functions -----------------------------------------------------------
    #: Ceiling assumed for every argument passed to a generated function. The
    #: call sites are built to respect it, so a body can be bounded without
    #: knowing which call it came from.
    ARG_BOUND = 1000.0

    def function(self, index: int) -> tuple[str, str, list[str], float]:
        """One helper function: (source, name, parameter types, return bound).

        Arity goes up to nine deliberately. Every ABI bug in this compiler was
        in argument passing, and none of them could happen with fewer than
        four arguments: System V and Microsoft x64 disagree about how the
        integer and SSE sequences are indexed, arguments past the register
        file go on the stack, and moving values into argument registers in
        argument order silently collapses them when one register is another's
        source. Two-argument calls exercise none of that.
        """
        r = self.rng
        name = f"fn{index}"
        arity = r.randint(0, 9)
        types = [r.choice(["int", "float"]) for _ in range(arity)]
        returns = r.choice(["int", "float"])

        # The body sees only its own parameters, so the caller's variables are
        # swapped out and restored -- a generated function must not reference
        # a name from main's scope, which the frontend has no closures for.
        saved_int, saved_float = self.int_vars, self.float_vars
        self.int_vars = {f"p{i}": self.ARG_BOUND
                         for i, t in enumerate(types) if t == "int"}
        self.float_vars = {f"p{i}": self.ARG_BOUND
                           for i, t in enumerate(types) if t == "float"}
        expr = self.int_expr() if returns == "int" else self.float_expr()
        bound = expr.bound
        self.int_vars, self.float_vars = saved_int, saved_float

        # EVERY parameter is read, and that is the point. An argument the body
        # ignores is an argument whose value nothing observes, so a call that
        # passed it in the wrong register would produce the right answer
        # anyway -- and the ABI bugs this is hunting would go unnoticed while
        # appearing to be covered.
        terms = [f"p{i}" if (t == "int") == (returns == "int")
                 else (f"int(p{i})" if returns == "int" else f"float(p{i})")
                 for i, t in enumerate(types)]
        body = " + ".join(terms + [f"({expr})"])
        bound += arity * self.ARG_BOUND

        params = ", ".join(f"p{i}: {t}" for i, t in enumerate(types))
        src = (f"def {name}({params}) -> {returns}:\n"
               f"    return {body}\n")
        return src, name, types, bound

    def call(self, name: str, types: list[str], bound: float) -> Bounded:
        """A call whose arguments respect `ARG_BOUND`."""
        args = []
        for t in types:
            if t == "int":
                v = self.rng.randint(-int(self.ARG_BOUND), int(self.ARG_BOUND))
                args.append(str(v) if v >= 0 else f"({v})")
            else:
                args.append(f"{self.rng.uniform(-self.ARG_BOUND, self.ARG_BOUND):.4f}")
        return Bounded(f"{name}({', '.join(args)})", bound)

    # -- whole program -------------------------------------------------------
    def program(self) -> str:
        r = self.rng
        functions: list[tuple[str, list[str], float, str]] = []
        preamble: list[str] = []
        for i in range(r.randint(0, 3)):
            src, name, types, bound = self.function(i)
            preamble.append(src)
            functions.append((name, types, bound,
                              "int" if "-> int" in src else "float"))

        lines: list[str] = ["def main() -> int:"]

        for i in range(r.randint(2, 4)):
            e = self.int_expr()
            lines.append(f"    i{i}: int = {e}")
            self.int_vars[f"i{i}"] = e.bound
        for i in range(r.randint(1, 3)):
            e = self.float_expr()
            lines.append(f"    f{i}: float = {e}")
            self.float_vars[f"f{i}"] = e.bound

        for _ in range(r.randint(3, 8)):
            kind = r.random()
            if functions and kind < 0.18:
                name, types, bound, returns = r.choice(functions)
                call = self.call(name, types, bound)
                if returns == "int":
                    lines.append(f"    print({call})")
                else:
                    lines.append(f"    print({call})")
            elif kind < 0.35:
                lines.append(f"    print({self.int_expr()})")
            elif kind < 0.52:
                lines.append(f"    print({self.float_expr()})")
            elif kind < 0.64:
                lines.append(f"    print(int({self.bool_expr()}))")
            elif kind < 0.76:
                lines.append(f"    if {self.bool_expr()}:")
                lines.append(f"        print({self.int_expr()})")
            elif kind < 0.86:
                # A while loop with a counter, which the `for` form never
                # produces: a different block shape reaching the same join.
                name = r.choice(list(self.int_vars))
                limit = r.randint(1, 5)
                lines.append(f"    w{limit}: int = 0")
                lines.append(f"    while w{limit} < {limit}:")
                lines.append(f"        w{limit} = w{limit} + 1")
                lines.append(f"        {name} = {name} + w{limit}")
                self.int_vars[name] += 15
                lines.append(f"    print({name})")
            else:
                name = r.choice(list(self.int_vars))
                lines.append(f"    for k in range({r.randint(1, 5)}):")
                lines.append(f"        {name} = {name} + k")
                self.int_vars[name] += 10        # 0+1+2+3+4 at most
                if r.random() < 0.4:
                    keyword = "break" if r.random() < 0.5 else "continue"
                    lines.append("        if k == 2:")
                    lines.append(f"            {keyword}")
                lines.append(f"    print({name})")

        lines.append("    return 0")
        return "\n".join(preamble + ["\n".join(lines)]) + "\n"


def render(value) -> str:
    """Format a printed value the way the runtime does. Only floats differ."""
    return f"{value:f}" if isinstance(value, float) else str(value)


def cpython_output(src: str) -> list[str]:
    out: list[str] = []
    ns = {"print": lambda *a: out.append(" ".join(render(x) for x in a))}
    exec(compile(src, "<generated>", "exec"), ns)
    ns["main"]()
    return out


def interpreter_output(src: str, tmp_path: Path, optimise: bool) -> list[str]:
    path = tmp_path / "gen.py"
    path.write_text(src, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(source=path, optimise=optimise), sink)
    assert result.ok, [d.message for d in sink.diagnostics] + [src]
    out = StringIO()
    Interpreter(result.module, out=out).run("main")
    return out.getvalue().split("\n")[:-1] if out.getvalue() else []


def compiled_output(src: str, tmp_path: Path, backend: str) -> list[str]:
    path = tmp_path / "gen.py"
    path.write_text(src, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(Options(
        source=path, output=tmp_path / "gen.exe", backend=backend, link=True,
        target=target_registry.get("c" if backend == "c" else HOST_TARGET),
        workdir=tmp_path / f"w_{backend}"), sink)
    assert result.ok, [d.message for d in sink.diagnostics] + [src]
    ran = subprocess.run([str(result.program)], capture_output=True, text=True)
    assert ran.returncode == 0, ran.stderr
    return ran.stdout.split("\n")[:-1]


SEEDS = list(range(40))


@pytest.mark.parametrize("seed", SEEDS)
def test_interpreter_matches_cpython(seed, tmp_path):
    src = ProgramGenerator(seed).program()
    assert interpreter_output(src, tmp_path, False) == cpython_output(src), src


@pytest.mark.parametrize("seed", SEEDS)
def test_optimised_matches_cpython(seed, tmp_path):
    """A pass that changes meaning shows up here and nowhere else."""
    src = ProgramGenerator(seed).program()
    assert interpreter_output(src, tmp_path, True) == cpython_output(src), src


@pytest.mark.skipif(not HAS_CC, reason="no C compiler available")
@pytest.mark.parametrize("seed", SEEDS[:20])
@pytest.mark.parametrize("backend", ["c", "x86-64"])
def test_compiled_matches_cpython(seed, backend, tmp_path):
    src = ProgramGenerator(seed).program()
    assert compiled_output(src, tmp_path, backend) == cpython_output(src), src


class TestTheGeneratorItself:
    """A generator that quietly produced nothing interesting would pass."""

    def test_it_is_deterministic(self):
        assert ProgramGenerator(7).program() == ProgramGenerator(7).program()

    def test_different_seeds_differ(self):
        assert ProgramGenerator(1).program() != ProgramGenerator(2).program()

    def test_it_exercises_the_operators_that_have_bitten(self):
        """Each of these was a real bug found in this compiler."""
        corpus = "".join(ProgramGenerator(s).program() for s in range(60))
        for fragment in ("//", "%", "**", "<<", ">>", "float(", "int(",
                         "break", "continue", "and", "or", "not "):
            assert fragment in corpus, f"the generator never emits {fragment!r}"

    def test_no_generated_value_overflows(self):
        """The bound is the reason these tests can compare against CPython at
        all, so it is checked rather than trusted."""
        for seed in range(200):
            src = ProgramGenerator(seed).program()
            for line in cpython_output(src):
                try:
                    assert abs(int(line)) < 2 ** 63, f"seed {seed}: {line}"
                except ValueError:
                    pass                      # a float; its bound is separate
