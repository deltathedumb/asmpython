"""The `cpyext` backend: does it produce a real CPython extension module.

`TestTheArtifact` checks the emitted C without a compiler (fast, always
runs). `TestTheExtensionActuallyLoads` needs `cc` and the running
interpreter's own headers -- it compiles, links, `dlopen`s the result
through CPython's own `importlib.util`, and calls into it, which is the
only way to tell a real extension module apart from C that merely looks
like one.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys

from tests import harness

from asmpython import backend as backend_registry
from asmpython import target as target_registry
from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source

backend_registry.load_builtin()
TARGET = target_registry.get("x86_64-linux-cpyext")

LIB = """\
def add(a, b):
    return a + b


def concat(a, b):
    return a + b


def add_typed(a: int, b: int) -> int:
    return a + b


def scale(x: float, k: float) -> float:
    return x * k


def is_positive(n: int) -> bool:
    return n > 0


def divide(a, b):
    return a / b


def with_default(a, b=1):
    return a + b


def with_star(*args):
    return len(args)
"""


def _msgs(sink: DiagnosticSink) -> str:
    # `BackendUnsupported`'s text lands in a NOTE (`driver/pipeline.py`
    # wraps it as `error("E9103", "... cannot compile ...").note(str(exc))`),
    # not in the diagnostic's own `.message` -- both are joined here so a
    # test can grep either without knowing which.
    return "\n".join(d.message + "\n" + "\n".join(d.notes)
                    for d in sink.diagnostics)


def _emit(tmp_path, source=LIB, *, library=True, name="prog", **backend_options):
    path = tmp_path / f"{name}.py"
    path.write_text(source, encoding="utf-8")
    sink = DiagnosticSink()
    result = compile_source(
        Options(source=path, backend="cpyext", target=TARGET, link=False,
               frontend_options={"library": library},
               backend_options=backend_options),
        sink)
    return result, sink


def _load(tmp_path, source=LIB, *, library=True, modname="prog", **backend_options):
    """Build, link with `cc`, and import the result into THIS interpreter."""
    path = tmp_path / f"{modname}.py"
    path.write_text(source, encoding="utf-8")
    so = tmp_path / f"{modname}.so"
    sink = DiagnosticSink()
    result = compile_source(
        Options(source=path, backend="cpyext", target=TARGET, output=so,
               link=True, frontend_options={"library": library},
               backend_options=backend_options),
        sink)
    assert result.ok, _msgs(sink)
    assert result.program is not None and result.program.exists()
    spec = importlib.util.spec_from_file_location(modname, result.program)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRegistration:
    def test_it_is_language_kind(self):
        # Emits C -- `cc -shared` finishes the job, same shape as `c`.
        assert backend_registry.get("cpyext").kind == "language"

    def test_it_is_self_contained(self):
        assert backend_registry.get("cpyext").self_contained


class TestTheArtifact:
    def test_one_c_file(self, tmp_path):
        result, sink = _emit(tmp_path)
        assert result.ok, _msgs(sink)
        assert list(result.artifacts) == ["prog.c"]

    def test_python_h_is_included_first(self, tmp_path):
        result, sink = _emit(tmp_path)
        assert result.ok, _msgs(sink)
        text = result.artifacts["prog.c"].decode("utf-8")
        assert text.startswith("#include <Python.h>")

    def test_pyinit_and_method_table_present(self, tmp_path):
        result, sink = _emit(tmp_path)
        assert result.ok, _msgs(sink)
        text = result.artifacts["prog.c"].decode("utf-8")
        assert "PyMODINIT_FUNC PyInit_prog(void)" in text
        assert '{"add", cpyext_call_add, METH_VARARGS, NULL}' in text

    def test_unsupported_signatures_are_not_exported(self, tmp_path):
        result, sink = _emit(tmp_path)
        assert result.ok, _msgs(sink)
        text = result.artifacts["prog.c"].decode("utf-8")
        assert "cpyext_call_with_default" not in text
        assert "cpyext_call_with_star" not in text

    def test_custom_module_name(self, tmp_path):
        result, sink = _emit(tmp_path, **{"module-name": "renamed"})
        assert result.ok, _msgs(sink)
        assert list(result.artifacts) == ["renamed.c"]
        assert "PyInit_renamed" in result.artifacts["renamed.c"].decode("utf-8")

    def test_bad_module_name_is_an_option_error(self, tmp_path):
        result, sink = _emit(tmp_path, **{"module-name": "not-an-identifier"})
        assert not result.ok
        assert "module-name" in _msgs(sink)


class TestRefusesRatherThanCrashes:
    def test_nothing_exportable_names_why(self, tmp_path):
        source = "def with_default(a, b=1):\n    return a + b\n"
        result, sink = _emit(tmp_path, source)
        assert not result.ok
        messages = _msgs(sink)
        assert "with_default" in messages
        assert "default" in messages

    def test_no_source_span_is_a_diagnostic(self):
        from asmpython.backend.base import BackendUnsupported
        from asmpython.backend import get
        from asmpython.ir import Module

        with harness.raises(BackendUnsupported, match="source"):
            get("cpyext").emit(Module(), TARGET)


@harness.needs("cc")
class TestTheExtensionActuallyLoads:
    """Compiles, links, and `import`s the result into the live interpreter
    running this test -- the only way to know the artifact is not merely
    C that looks like an extension module."""

    def test_dynamic_int_function(self, tmp_path):
        mod = _load(tmp_path)
        assert mod.add(3, 4) == 7
        assert isinstance(mod.add(3, 4), int)

    def test_dynamic_string_function(self, tmp_path):
        mod = _load(tmp_path)
        assert mod.concat("hello, ", "world") == "hello, world"

    def test_typed_int_function(self, tmp_path):
        mod = _load(tmp_path)
        assert mod.add_typed(3, 4) == 7

    def test_typed_float_function(self, tmp_path):
        mod = _load(tmp_path)
        assert mod.scale(2.5, 4.0) == 10.0

    def test_typed_bool_return(self, tmp_path):
        mod = _load(tmp_path)
        assert mod.is_positive(5) is True
        assert mod.is_positive(-5) is False

    def test_bad_argument_type_raises_typeerror(self, tmp_path):
        mod = _load(tmp_path)
        with harness.raises(TypeError):
            mod.add(3, "x")

    def test_wrong_arity_raises_typeerror(self, tmp_path):
        mod = _load(tmp_path)
        with harness.raises(TypeError):
            mod.add(3)

    def test_asmpython_runtime_error_becomes_the_right_python_exception(self, tmp_path):
        mod = _load(tmp_path)
        with harness.raises(ZeroDivisionError):
            mod.divide(1, 0)

    def test_module_survives_source_deletion(self, tmp_path):
        """Proves the loaded module is the compiled artifact, not the
        `.py` still sitting next to it -- deleting the source and calling
        the function again can only work if CPython loaded the `.so`."""
        mod = _load(tmp_path)
        (tmp_path / "prog.py").unlink()
        assert mod.add(10, 32) == 42

    def test_bare_import_via_sys_path(self, tmp_path):
        """The full, ordinary experience: `import modname`, no explicit
        loader -- proves `PyInit_<name>` and the file's own suffix are
        both right, not just that `spec_from_file_location` can force it."""
        path = tmp_path / "sysmod.py"
        path.write_text(LIB, encoding="utf-8")
        suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
        so = tmp_path / f"sysmod{suffix}"
        sink = DiagnosticSink()
        result = compile_source(
            Options(source=path, backend="cpyext", target=TARGET, output=so,
                   link=True, frontend_options={"library": True}),
            sink)
        assert result.ok, _msgs(sink)
        path.unlink()
        sys.path.insert(0, str(tmp_path))
        sys.modules.pop("sysmod", None)
        try:
            import sysmod
            assert sysmod.add(1, 2) == 3
        finally:
            sys.modules.pop("sysmod", None)
            sys.path.remove(str(tmp_path))
