"""`compile`, `eval` and `exec` inside a compiled program agree with CPython.

WHAT THIS COVERS that the unit tests do not: the SPLICE. `test_bundled_compile`
imports `_pycompile` as ordinary Python and checks its answers; this compiles a
program that NAMES `compile`, which pulls `_pylex`, `_pyast`, `_pyparse`,
`_pyvalidate`, `_pycompile` and `_pyrun` into it as definitions, and then runs
the result. Everything between the name and the answer is under test.

WHY THE INTERPRETER AND NOT THE CORPUS. Splicing two and a half thousand lines
of Python costs 22 seconds to compile and far longer again to run through the
IR interpreter; putting it in `test_dynamic_python.py` took a corpus run from
two and a half minutes to over six, and the corpus is the loop to stay inside.
The C backend is covered by the nineteen conformance cases that call these
three, which is where their behaviour is actually specified.
"""
from __future__ import annotations

import subprocess
import sys
from io import StringIO
from pathlib import Path

from tests import harness

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter

#: The program, kept as a file so CPython can run the very same bytes.
PROGRAM = Path(__file__).with_name("_runtime_compiler_program.py")


def _cpython() -> list[str]:
    done = subprocess.run([sys.executable, str(PROGRAM)],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.replace("\r\n", "\n").split("\n")[:-1]


def _compiled(sink: DiagnosticSink):
    result = compile_source(Options(source=PROGRAM), sink)
    assert result.ok, [d.message for d in sink.diagnostics]
    return result.module


class TestTheProgramAgrees:
    def test_the_interpreter_matches_cpython(self):
        """The whole path: splice, parse, validate, walk."""
        out = StringIO()
        Interpreter(_compiled(DiagnosticSink()), out=out).run("main")
        assert out.getvalue().split("\n")[:-1] == _cpython()

    def test_it_warns_where_the_call_is_written(self):
        """W0091 IS THE POINT of allowing these at all: the program compiles,
        and the reader is told what it will cost. One warning per name, at the
        call rather than at the top of the file."""
        sink = DiagnosticSink()
        _compiled(sink)
        said = {d.code for d in sink.diagnostics}
        assert said == {"W0091"}, said
        messages = sorted(d.message for d in sink.diagnostics)
        assert len(messages) == 3, messages          # compile, eval and exec
        assert any("compile()" in m for m in messages)
        assert any("eval()" in m for m in messages)
        assert any("exec()" in m for m in messages)

    def test_a_program_that_names_none_of_them_splices_nothing(self):
        """The cost is paid by the program that asks and by no other."""
        import tempfile
        path = Path(tempfile.mkdtemp()) / "plain.py"
        path.write_text("x = 1\nprint(x + 1)\n", encoding="utf-8")
        sink = DiagnosticSink()
        module = compile_source(Options(source=path), sink).module
        assert not [fn for fn in module.functions
                    if "_pycompile" in fn.name or "_pyparse" in fn.name]
        assert not sink.diagnostics
