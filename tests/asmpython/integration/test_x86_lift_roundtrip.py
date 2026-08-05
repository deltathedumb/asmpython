"""IR out through the x86-64 backend, back in through the lifter, run both.

A lifter cannot be verified by reading it. Its failures divide into refusals,
which announce themselves, and MISREADINGS, which do not: lift `%eax` as if it
were `%rax` and the answer is right until a value exceeds 32 bits; lift a
float-returning function as returning `%rax` and it returns zero, which is a
number. Both produce a program that runs.

So the property tested here is a round trip. The same module is executed twice
-- once as the frontend built it, once after a backend has turned it into
assembly and the lifter has turned that back into IR -- and the two runs must
print the same thing. Any disagreement is a real defect in one of the three,
localised to one generated program, and no expected output has to be written
down for it to be caught.

WHY THE GENERATED CORPUS rather than examples: it is the same corpus that
found the parallel-move collapse and the float `%` sign error (see
`test_differential.py`), and reusing it means the lifter is tried against
programs written with no knowledge of it. Every case it covers is a shape the
compiler actually emits, in the proportions it actually emits them -- forty
programs contain four hundred `xorpd`, which is how the first version's
"xorpd is only lifted as the zeroing idiom" turned out to reject every
program containing a unary minus on a float.

The lifter refuses stack-passed arguments, and that is a limit rather than a
bug: a lifted function's frame is one allocation private to it, and the
seventh argument onward lives in memory two frames share. Those programs are
counted and skipped, not silently passed -- a suite that reports a refusal as
success is how a lifter comes to cover less than anyone thinks.
"""
from __future__ import annotations

import sys
from io import StringIO

import pytest

from asmpython import target as target_registry
from asmpython.diagnostics import DiagnosticSink, SourceFile
from asmpython.driver import Options, compile_source
from asmpython.frontends.x86.lift import ABI_INT_ARGS, lift_module
from asmpython.frontends.x86.parse import parse
from asmpython.ir import types as T
from asmpython.ir.interpreter import Interpreter
from asmpython.ir.verifier import verify

from .test_differential import ProgramGenerator

def host_functions(abi: str) -> dict[str, tuple]:
    """The host functions the frontend and backend emit calls to.

    A call site records a symbol and nothing else -- not arity, not types --
    so these cannot be inferred from the assembly and are declared instead.
    `fmod` is on the list because it is not in the IR at all: every backend
    lowers a float remainder to a libm call, so it appears on the way back in
    without ever having been on the way out.

    Built from the ABI rather than written down, because the integer argument
    register is `%rdi` under System V and `%rcx` under Microsoft x64. A table
    that hardcodes one is wrong on the other platform in the quietest way
    available: the call still lifts, still runs, and reads a register the
    caller never set -- which cost this test an afternoon of blaming the
    lifter for printing nothing.
    """
    first = ABI_INT_ARGS[abi][0]
    return {
        "put_int":     (T.VOID, (first,)),
        "put_float":   (T.VOID, ("xmm0",)),
        "print_int":   (T.VOID, (first,)),
        "print_float": (T.VOID, ("xmm0",)),
        "print_str":   (T.VOID, (first,)),
        "putchar":     (T.I64,  (first,)),
        "pow":         (T.F64,  ("xmm0", "xmm1")),
        "fmod":        (T.F64,  ("xmm0", "xmm1")),
    }

HOST_TARGET = "x86_64-windows" if sys.platform == "win32" else "x86_64-linux"
#: The lifter must be told which convention the assembly was emitted for --
#: it cannot be read off the text. Getting it wrong is not a crash and not a
#: refusal: parameter inference looks for %rdi/%rsi in code that passes
#: arguments in %rcx/%rdx, finds no parameters, and lifts every function as
#: taking none, so each call passes nothing and the callee reads a register
#: nobody wrote. Discovered here by hardcoding System V and watching half the
#: corpus trap on an uninitialised register.
HOST_ABI = "win64" if sys.platform == "win32" else "sysv"

SEEDS = list(range(40))


def _interpret(module, entry: str) -> list[str]:
    out = StringIO()
    Interpreter(module, out=out).run(entry)
    text = out.getvalue()
    return text.split("\n")[:-1] if text else []


def _compile_to_assembly(src: str, tmp_path):
    target_registry.load_builtin()
    path = tmp_path / "gen.py"
    path.write_text(src, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(
        Options(source=path, backend="x86-64",
                target=target_registry.get(HOST_TARGET)), sink)
    assert result.ok, [d.message for d in sink.diagnostics] + [src]
    return result.module, result.artifacts["out.s"].decode()


def _lift(assembly: str):
    """Parse and lift, or return the diagnostics explaining why not."""
    sink = DiagnosticSink()
    statements = parse(SourceFile(assembly), sink)
    assert statements is not None, [d.message for d in sink.diagnostics]
    sink = DiagnosticSink()
    module = lift_module(statements, sink, abi=HOST_ABI,
                         declared=host_functions(HOST_ABI))
    return module, [d.message for d in sink.diagnostics]


#: Refusals this lifter documents rather than defects. Matched as substrings
#: so a program that hits one is skipped; anything else fails the test, which
#: is what keeps the skip list from quietly absorbing new gaps.
DOCUMENTED_LIMITS = (
    "outgoing-argument area",
    "caller's frame",
)


class TestTheRoundTripPreservesMeaning:
    @pytest.mark.parametrize("seed", SEEDS)
    def test_lifted_assembly_prints_what_the_ir_printed(self, seed, tmp_path):
        src = ProgramGenerator(seed).program()
        module, assembly = _compile_to_assembly(src, tmp_path)
        expected = _interpret(module, "main")

        lifted, why = _lift(assembly)
        if lifted is None:
            if any(limit in message
                   for message in why for limit in DOCUMENTED_LIMITS):
                pytest.skip(f"documented limit: {why[0]}")
            pytest.fail(f"seed {seed}: {why}\n{src}")

        assert verify(lifted) is None, verify(lifted)
        # The backend renames the entry point, because `main` in the object
        # file is the C runtime's.
        entry = "asmpython_main" if lifted.function("asmpython_main") else "main"
        assert _interpret(lifted, entry) == expected, src


class TestTheCorpusReachesTheLifter:
    """A round trip that skipped everything would pass silently.

    These pin the coverage the parametrised test above actually gets, so a
    change that starts refusing half the corpus fails here rather than
    turning into forty quiet skips.
    """

    #: How much of the corpus each convention can reach, and they differ for a
    #: reason that is not the lifter's: System V carries six integer arguments
    #: in registers and Microsoft x64 carries four, so the SAME generated
    #: function spills to the stack on one platform and not the other. The
    #: floors are set just under what is currently achieved, so losing a few
    #: programs fails here while the ordinary variation between runs does not.
    MINIMUM_LIFTED = {"sysv": 34, "win64": 19}

    def test_most_of_the_corpus_lifts(self, tmp_path):
        lifted_count = 0
        for seed in SEEDS:
            src = ProgramGenerator(seed).program()
            _, assembly = _compile_to_assembly(src, tmp_path)
            module, _ = _lift(assembly)
            lifted_count += module is not None
        assert lifted_count >= self.MINIMUM_LIFTED[HOST_ABI]

    def test_the_corpus_exercises_the_instructions_that_were_broken(
            self, tmp_path):
        """Each of these was refused or mislifted the first time it ran."""
        seen: set[str] = set()
        for seed in SEEDS[:10]:
            src = ProgramGenerator(seed).program()
            _, assembly = _compile_to_assembly(src, tmp_path)
            for line in assembly.splitlines():
                text = line.strip()
                if text and not text.startswith((".", "#")) \
                        and not text.endswith(":"):
                    seen.add(text.split()[0])
        for mnemonic in ("xorpd", "setnp", "jne", "setl", "cvtsi2sdq",
                         "movzbq", "idivq", "ucomisd"):
            assert mnemonic in seen, mnemonic
