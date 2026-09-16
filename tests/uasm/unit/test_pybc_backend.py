"""The `pybc` backend: does it produce a real, host-loadable `.pyc`.

Unlike every other backend's tests, correctness here is not "matches
CPython" -- it IS CPython's own `compile()`, so the interesting questions
are: does the round trip through the full `uasm build` pipeline
actually produce bytes the running interpreter accepts (`test_the_pyc_runs`),
do they match what `compile()`/`marshal` would have written directly for
the same source (`test_matches_a_direct_compile`), does `--opt-level`
reach CPython's own `-O`/`-OO` (`test_opt_level_strips_docstrings`), and
does a module with no recoverable source refuse rather than crash
(`test_refuses_without_source`).
"""
from __future__ import annotations

import marshal
import struct
import subprocess
import sys

from tests import harness

from uasm import backend as backend_registry
from uasm import target as target_registry
from uasm.diagnostics import DiagnosticSink
from uasm.driver import Options, compile_source

backend_registry.load_builtin()
TARGET = target_registry.get("pybc")

SOURCE = """\
def add(a: int, b: int) -> int:
    return a + b


def main() -> int:
    print(add(3, 4))
    return 0


if __name__ == "__main__":
    main()
"""


def _msgs(sink: DiagnosticSink) -> str:
    return "\n".join(d.message for d in sink.diagnostics)


def _build(tmp_path, source=SOURCE, *, link=True, **backend_options):
    path = tmp_path / "prog.py"
    path.write_text(source, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(
        Options(source=path, backend="pybc", target=TARGET, link=link,
               backend_options=backend_options),
        sink)
    return result, sink


class TestRegistration:
    def test_it_is_ready(self):
        be = backend_registry.get("pybc")
        assert be.ready

    def test_it_is_binary(self):
        assert backend_registry.get("pybc").kind == "binary"

    def test_it_is_self_contained(self):
        # A `.pyc` needs nothing linked in -- the host CPython IS the
        # runtime, the same way a `.jar` needs a JVM and nothing else.
        assert backend_registry.get("pybc").self_contained


class TestTheArtifact:
    def test_one_pyc_artifact(self, tmp_path):
        result, sink = _build(tmp_path, link=False)
        assert result.ok, _msgs(sink)
        names = list(result.artifacts)
        assert names == ["prog.pyc"], names

    def test_the_magic_number_is_the_hosts(self, tmp_path):
        import importlib.util
        result, sink = _build(tmp_path, link=False)
        assert result.ok, _msgs(sink)
        data = result.artifacts["prog.pyc"]
        assert data[:4] == importlib.util.MAGIC_NUMBER

    def test_the_header_is_pep_552_shaped(self, tmp_path):
        """4-byte magic, 4-byte bit field, then an 8-byte timestamp/size
        pair (bit 0 unset) or a hash (bit 0 set) -- either way, 16 bytes
        before the marshalled code object begins."""
        result, sink = _build(tmp_path, link=False)
        assert result.ok, _msgs(sink)
        data = result.artifacts["prog.pyc"]
        bit_field = struct.unpack_from("<L", data, 4)[0]
        assert bit_field == 0
        code = marshal.loads(data[16:])
        assert code.co_filename.endswith("prog.py")

    def test_deterministic(self, tmp_path):
        # THE SOURCE FILE IS WRITTEN ONCE. The header carries the source's
        # own mtime (PEP 552), so writing it a second time between builds
        # would make two otherwise-identical compiles disagree for a real
        # reason (the file genuinely changed) rather than test anything
        # about this backend.
        path = tmp_path / "prog.py"
        path.write_text(SOURCE, encoding="utf-8")
        opts = Options(source=path, backend="pybc", target=TARGET, link=False)
        a = compile_source(opts, DiagnosticSink())
        b = compile_source(opts, DiagnosticSink())
        assert a.ok and b.ok
        assert a.artifacts == b.artifacts


class TestTheProgramRuns:
    def test_the_pyc_runs(self, tmp_path):
        result, sink = _build(tmp_path)
        assert result.ok, _msgs(sink)
        assert result.program is not None
        assert result.program.name == "prog.pyc"
        ran = subprocess.run([sys.executable, str(result.program)],
                             capture_output=True, text=True)
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout == "7\n"

    def test_matches_a_direct_compile(self, tmp_path):
        """Byte-identical to what `compile()` + `marshal` would have
        written for the same text under the same filename -- the whole
        point of using the host's own compiler rather than a hand-rolled
        one is that there is no second implementation to disagree with
        the first."""
        result, sink = _build(tmp_path, link=False)
        assert result.ok, _msgs(sink)
        got = result.artifacts["prog.pyc"]
        want_code = compile((tmp_path / "prog.py").read_text(encoding="utf-8"),
                            str(tmp_path / "prog.py"), "exec",
                            dont_inherit=True, optimize=0)
        assert marshal.loads(got[16:]).co_code == want_code.co_code
        assert marshal.loads(got[16:]).co_consts == want_code.co_consts

    def test_opt_level_strips_docstrings(self, tmp_path):
        source = '"""a module docstring."""\n\n\ndef main() -> int:\n    return 0\n'
        plain, sink = _build(tmp_path, source, link=False, **{"opt-level": "0"})
        stripped, sink2 = _build(tmp_path, source, link=False, **{"opt-level": "2"})
        assert plain.ok and stripped.ok, (_msgs(sink), _msgs(sink2))
        plain_code = marshal.loads(plain.artifacts["prog.pyc"][16:])
        stripped_code = marshal.loads(stripped.artifacts["prog.pyc"][16:])
        assert "a module docstring." in plain_code.co_consts
        assert "a module docstring." not in stripped_code.co_consts

    def test_bad_opt_level_is_an_option_error(self, tmp_path):
        result, sink = _build(tmp_path, link=False, **{"opt-level": "3"})
        assert not result.ok
        assert "opt-level" in _msgs(sink)


class TestRefusesRatherThanCrashes:
    def test_no_source_span_is_a_diagnostic(self):
        from uasm.backend.base import BackendUnsupported
        from uasm.backend import get
        from uasm.ir import Module

        with harness.raises(BackendUnsupported, match="source"):
            get("pybc").emit(Module(), TARGET)
