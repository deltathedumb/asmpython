"""The integer cell, ported to the machine subset.

STAGE 3 OF `docs/INERT-RUNTIME.md`: the first piece of asmpython's object
runtime that is not C. `src/asmpython/runtime/int_cell.py` is compiled by
asmpython's own frontend and spliced into every program that reaches the
runtime, and the C definitions it replaces are turned into declarations so the
two cannot both be linked.

WHAT THESE TESTS ARE FOR, in order of how much they matter:

1. THE LAYOUT CANNOT DRIFT. A cell the subset builds is read by 15,000 lines of
   C that were not told anything changed. The offsets in `int_cell.py` are
   therefore compiled out of that C and compared, rather than trusted.
2. THE SWITCH IS COHERENT. A name declared ported that is not defined, or
   defined and not omitted from the C, is a duplicate symbol at LINK time --
   which names an object file rather than the list that was wrong.
3. THE BEHAVIOUR IS CPYTHON'S. Small-integer sharing is observable, and the
   ported cache has to share exactly what CPython shares, including at the
   boundary: 256 is shared and 257 is not, and a program can see which.
4. NOTHING ELSE PAYS. A statically typed program must not acquire the object
   runtime because the splice exists.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

from tests import harness
from tests.harness import snapshot

SRC = snapshot.current(Path(__file__).resolve().parents[3])


def _cli(*args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run([sys.executable, "-m", "asmpython", *args],
                          capture_output=True, text=True, env=env)


def write(tmp_path: Path, source: str, name: str = "prog.py") -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


def build_and_run(tmp_path: Path, source: Path) -> subprocess.CompletedProcess:
    out = tmp_path / "prog.exe"
    built = _cli("build", str(source), "--backend", "c", "-o", str(out),
                 "--workdir", str(tmp_path / "wd"))
    assert built.returncode == 0, built.stdout + built.stderr
    return subprocess.run([str(out)], capture_output=True, text=True)


class TestTheLayoutIsTheCs:
    """Compiled out of the C, not written down twice."""

    @harness.needs("gcc")
    def test_the_offsets_match_the_struct(self, tmp_path):
        """`sizeof`, `offsetof(kind)` and `offsetof(v.i)` as the C compiler
        computes them, against what `runtime/int_cell.py` assumes.

        A PROBE RATHER THAN A PARSE. The union has twenty members and its size
        follows from padding rules no reader should have to apply; asking the
        compiler is the only answer that is right by construction, and it is
        also what a reader would do to check.
        """
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link.runtime import runtime_c
        finally:
            del sys.path[0]

        probe = runtime_c(entry="probe_entry")
        probe = probe.replace("extern int64_t probe_entry(void);", "")
        probe = probe.replace(
            "int main(void) { return (int)probe_entry(); }",
            '#include <stddef.h>\n'
            'int main(void) {\n'
            '    printf("%zu %zu %zu\\n", sizeof(apy_obj),\n'
            '           offsetof(apy_obj, kind), offsetof(apy_obj, v.i));\n'
            '    return 0;\n'
            '}\n')
        # `runtime_c()` with no module keeps every C definition -- the switch
        # is per-build and this build has no IR to stand aside for -- so the
        # probe compiles the runtime exactly as an unported program would and
        # needs no stubs. It only asks the compiler about the struct.
        c = tmp_path / "probe.c"
        c.write_text(probe, encoding="utf-8")
        exe = tmp_path / "probe.exe"
        built = subprocess.run(["gcc", "-w", str(c), "-o", str(exe)],
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stderr[-3000:]
        got = subprocess.run([str(exe)], capture_output=True, text=True)
        size, kind, payload = (int(x) for x in got.stdout.split())

        source = (Path(SRC) / "asmpython" / "runtime" / "int_cell.py").read_text(
            encoding="utf-8")

        def literal(fn: str) -> int:
            m = re.search(rf"def {fn}\(\) -> i64:\n    return (-?\d+)", source)
            assert m, f"{fn} is not a one-line constant any more"
            return int(m.group(1))

        assert literal("apy_obj_size") == size, (
            f"the C struct is {size} bytes and int_cell.py says "
            f"{literal('apy_obj_size')}")
        assert literal("apy_kind_offset") == kind
        assert literal("apy_payload_offset") == payload


class TestTheSwitchIsCoherent:
    """A half-applied port is a link error naming the wrong thing."""

    def test_every_replaced_name_is_actually_defined(self):
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link import objects_ir
            assert objects_ir.check() == []
        finally:
            del sys.path[0]

    def test_the_c_no_longer_defines_what_the_ir_does(self):
        """The C keeps a DECLARATION, which its own hundred-odd callers need,
        and loses the body. Removing it outright would make every caller an
        implicit declaration returning `int`."""
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link.objects import objects_c
            from asmpython.link.objects_ir import PORTED
            text = objects_c(omit=PORTED)
        finally:
            del sys.path[0]
        for name in PORTED:
            defs = [l for l in text.splitlines()
                    if l.startswith("APY_API") and f" {name}(" in l]
            assert len(defs) == 1, (name, defs)
            assert defs[0].rstrip().endswith("*/"), defs[0]
            assert "{" not in defs[0], f"{name} still has a body: {defs[0]}"

    def test_the_runtime_source_compiles_on_the_static_path(self):
        """It must emit no `apy_*` it did not name, and no object at all.

        The static path is the one that stands on the machine rather than on
        the runtime -- that is the whole reason the port is written in it --
        so a runtime module that had drifted onto the dynamic path would be
        depending on the thing it is replacing.
        """
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.link import objects_ir
            module = objects_ir.compile_runtime()
        finally:
            del sys.path[0]
        defined = {f.name for f in module.functions if not f.external}
        assert "apy_from_int" in defined and "apy_as_int" in defined
        external = {f.name for f in module.functions if f.external}
        assert external <= {"plat_heap", "plat_write", "plat_exit"}, external


class TestItBehavesLikeCPython:
    """Small-integer sharing is observable, so it has to be exact."""

    @harness.needs("gcc")
    def test_the_cache_shares_exactly_what_cpython_shares(self, tmp_path):
        """-5..256 shared, 257 not. The boundary is the point: a cache that
        was one wider or one narrower passes every test that does not look at
        it and fails `a = 257; b = 257; a is b`."""
        program = """\
            for n in (-6, -5, 0, 1, 256, 257):
                a = n + 0
                b = n + 0
                print(n, a is b)
        """
        got = build_and_run(tmp_path, write(tmp_path, program))
        assert got.returncode == 0, got.stderr
        assert got.stdout.split() == [
            "-6", "False", "-5", "True", "0", "True", "1", "True",
            "256", "True", "257", "False"], got.stdout

    @harness.needs("gcc")
    def test_arithmetic_through_the_ported_constructor(self, tmp_path):
        """Everything the runtime builds an int with goes through it -- every
        literal, every length, every loop counter -- so a wrong cell is not a
        subtle failure."""
        program = """\
            print(sum(range(100)))
            print(len([1, 2, 3]) * 7)
            print((2 ** 70) // 3)
            print(-9223372036854775808 + 1)
            print(int("12345") + 1)
        """
        got = build_and_run(tmp_path, write(tmp_path, program))
        assert got.returncode == 0, got.stderr
        assert got.stdout.split("\n")[:5] == [
            "4950", "21", "393530540239137101141", "-9223372036854775807",
            "12346"]


class TestNothingElsePays:
    """The splice must not give the object runtime to programs that had none."""

    def test_a_program_with_no_runtime_at_all_gets_nothing(self, tmp_path):
        """A program that links none of the C gets none of the IR either.

        The condition is `link.runtime.needs_runtime`, which is exactly
        "will the object runtime's C be in this build". A statically typed
        program that PRINTS does link it -- for `put_int` -- and `objects_c()`
        comes with it, so it needs the ported definitions even though its own
        code never names one. That is not the splice being greedy; it is what
        the C runtime being included whole already meant.
        """
        source = write(tmp_path, """\
            def main() -> int:
                return 6 * 7
        """)
        emitted = _cli("build", str(source), "--emit-ir",
                       "-o", str(tmp_path / "prog.ir"),
                       "--workdir", str(tmp_path / "wd"))
        assert emitted.returncode == 0, emitted.stdout + emitted.stderr
        text = (tmp_path / "prog.ir").read_text(encoding="utf-8")
        assert "apy_" not in text, text

    def test_a_dynamic_program_gets_the_ported_definitions(self, tmp_path):
        source = write(tmp_path, "print(1 + 1)\n")
        emitted = _cli("build", str(source), "--emit-ir",
                       "-o", str(tmp_path / "prog.ir"),
                       "--workdir", str(tmp_path / "wd"))
        assert emitted.returncode == 0, emitted.stdout + emitted.stderr
        text = (tmp_path / "prog.ir").read_text(encoding="utf-8")
        # DEFINED, not declared: `func apy_from_int(...) -> ptr {`, with a body.
        assert re.search(r"func apy_from_int\([^)]*\) -> ptr \{", text), \
            "apy_from_int is not defined in the spliced IR"
        assert "apy_small_ir" in text, "the small-int cache global is missing"


class TestTheCRuntimeIsStillSupported:
    """The port REMOVES an obligation; it must not create one.

    The reason to write the object runtime in asmpython's own subset is that a
    backend should not HAVE to define 229 functions. That is an argument for
    making the C unnecessary -- not for making it unavailable. So the whole
    hand-written C runtime remains a supported arrangement, reachable two ways:

        --object-runtime c          the whole build
        Backend.object_runtime      one function, for a backend that has its own

    Both are tested here rather than left as a flag nobody exercises, because
    an untested opt-out is one that stops working the first time the ported set
    grows -- and it will grow every stage from here.
    """

    def test_the_default_is_the_ported_runtime(self, tmp_path):
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.driver import Options
            assert Options(source=tmp_path / "x.py").object_runtime == "ir"
        finally:
            del sys.path[0]

    @harness.needs("gcc")
    def test_both_arrangements_agree(self, tmp_path):
        """The point of the switch: the same program, either runtime, one
        answer. A divergence here is a defect in the port, and this is the
        cheapest place it can be caught."""
        program = """\
            a = 1
            b = 1
            print(a is b, a + b)
            x = 257
            y = 257
            print(x is y, x + y)
            print(sum(range(20)), len("hello") * 3)
        """
        source = write(tmp_path, program)
        outputs = {}
        for mode in ("ir", "c"):
            out = tmp_path / f"prog_{mode}.exe"
            built = _cli("build", str(source), "--backend", "c",
                         "--object-runtime", mode, "-o", str(out),
                         "--workdir", str(tmp_path / f"wd_{mode}"))
            assert built.returncode == 0, built.stdout + built.stderr
            ran = subprocess.run([str(out)], capture_output=True, text=True)
            assert ran.returncode == 0, ran.stderr
            outputs[mode] = ran.stdout
        assert outputs["ir"] == outputs["c"], outputs
        assert outputs["ir"].split("\n")[0] == "True 2"

    def test_c_mode_splices_nothing_and_keeps_every_c_definition(self, tmp_path):
        source = write(tmp_path, "print(1 + 1)\n")
        emitted = _cli("build", str(source), "--emit-ir", "--object-runtime", "c",
                       "-o", str(tmp_path / "prog.ir"),
                       "--workdir", str(tmp_path / "wd"))
        assert emitted.returncode == 0, emitted.stdout + emitted.stderr
        text = (tmp_path / "prog.ir").read_text(encoding="utf-8")
        assert not re.search(r"func apy_from_int\([^)]*\) -> ptr \{", text), \
            "apy_from_int was spliced despite --object-runtime c"
        assert "apy_small_ir" not in text, "the cache global was spliced anyway"
        # And with nothing spliced, the C keeps its bodies -- which is what
        # makes the two halves of the switch impossible to get out of step:
        # the C omits exactly what the module defines, so no splice is no
        # omission.
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.ir.printer import parse_module
            from asmpython.link.objects import objects_c
            from asmpython.link.objects_ir import omitted_by
            module = parse_module(text)
            assert omitted_by(module) == ()
            c = objects_c(omit=omitted_by(module))
            assert "APY_API apy_value apy_from_int(int64_t i) {" in c
        finally:
            del sys.path[0]

    def test_a_backend_may_define_one_itself(self, tmp_path):
        """`Backend.object_runtime` is per FUNCTION, so a backend keeps the one
        it has an opinion about and takes the rest.

        Checked against the splice directly rather than through a real backend:
        no built-in one claims anything today -- that is what makes the shipped
        arrangement the ported one -- and a test that needed one to exist would
        be testing a fixture.
        """
        sys.path.insert(0, str(SRC))
        try:
            from asmpython.diagnostics import DiagnosticSink
            from asmpython.driver import Options, compile_source
            from asmpython.link import objects_ir
            path = tmp_path / "prog.py"
            path.write_text("print(1 + 1)\n", encoding="utf-8")
            sink = DiagnosticSink()
            # Compiled with the splice OFF, then spliced by hand, so that this
            # asks about `splice` rather than about the driver's plumbing.
            result = compile_source(
                Options(source=path, emit_ir=True, object_runtime="c"), sink)
            assert result.ok, [d.message for d in sink.diagnostics]
            module = result.module
            objects_ir.splice(module, provided=frozenset({"apy_from_int"}))
            defined = {f.name for f in module.functions if not f.external}
            assert "apy_from_int" not in defined, \
                "the backend said it defines this one"
            assert "apy_as_int" in defined, \
                "the rest of the runtime should still arrive"
            # And the C is then asked to stand aside for exactly that rest.
            assert objects_ir.omitted_by(module) == ("apy_as_int",)
        finally:
            del sys.path[0]


class TestTheInterpreterKeepsItsOwn:
    """Documented because it is a finding, not a preference.

    `objects_host.py` represents an `apy_value` as a HANDLE and the ported code
    represents one as an ADDRESS, so the two cannot be mixed -- the port is
    all-or-nothing on the interpreter path. Asserted so that a later change
    which quietly starts running spliced IR there fails here, with this
    explanation, rather than as `2248 is not a runtime value handle`.
    """

    def test_the_host_runtime_still_answers(self, tmp_path):
        source = write(tmp_path, "a = 1\nb = 1\nprint(a is b, a + b)\n")
        got = _cli("run", str(source))
        assert got.returncode == 0, got.stderr
        assert got.stdout.strip().endswith("True 2"), got.stdout
