"""Unit coverage for the packaged plugin formats (`_compiler/apkg.py`):
.apx (Extension) / .apb (Backend) / .apl (Linker) / .apmlc (mlang Config) /
.apm (bundle of any combination + on_load hook).

Fixtures are built in-memory (zipfile over BytesIO) and written to a
TemporaryDirectory -- no binary fixtures are committed to the repo.

Run: python -m unittest tests.test_apkg
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import asmpython
from asmpython._backends import get_backend
from asmpython._compiler.packaging import apkg
from asmpython._compiler.__main__ import (
    _load_backend_plugin,
    _load_linker_plugin,
    _resolve_backend_flag,
    _resolve_ext_flags,
    _resolve_linker_flag,
)
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler import ast_nodes as A
from asmpython._linkers import get_linker
from asmpython.mlang import Config


def _write_zip(tmpdir: Path, name: str, files: dict) -> Path:
    """`files` maps zip-relative name -> str content. Returns the path."""
    path = tmpdir / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for member_name, content in files.items():
            zf.writestr(member_name, content)
    path.write_bytes(buf.getvalue())
    return path


_LET_EXT_SRC = """
import asmpython
from asmpython._compiler import ast_nodes as A

def handle_let(parser, pos):
    name = parser._expect("NAME").value
    parser._expect("OP", "=")
    value = parser._parse_expr()
    parser._expect("NEWLINE")
    return A.Assign(target=name, value=value, pos=pos)

asmpython.extend.Extension(id={id!r}, statement_handlers={{"let": handle_let}})
"""

_DUMMY_BACKEND_SRC = """
import asmpython

class _Dummy:
    default_linker = "gcc"
    def compile(self, module, args):
        return {{"out": b"OBJ"}}
    def link(self, objects, args):
        return {{"out": b"LINKED"}}

asmpython.backend.Backend(name={name!r}, impl=_Dummy())
"""

_DUMMY_LINKER_SRC = """
import asmpython

class _Dummy:
    def link(self, ctx):
        return b"LINKED"

asmpython.linker.Linker(name={name!r}, impl=_Dummy())
"""

_MLANG_CONFIG_SRC = """
from asmpython.mlang import Config

cfg = Config(exe="rustc", frontend="rust", compile_args=["-o", "{{out}}", "{{src}}"], infer_signatures=True)
"""


class ApxTests(unittest.TestCase):
    def test_manifest_optional_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "fixture.apx", {"plugin.py": _LET_EXT_SRC.format(id="apx_no_manifest")})
            ids = _resolve_ext_flags([str(path)])
            self.assertEqual(ids, frozenset({"apx_no_manifest"}))
            mod = Parser(Lexer("let z = 9\nprint(z)\n").tokenize(), ids).parse()
            self.assertTrue(any(isinstance(s, A.Assign) and s.target == "z" for s in mod.body))

    def test_manifest_present_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manifest = json.dumps({"format": "asmpython.apkg", "version": 1, "kind": "extension", "entry": "main.py"})
            path = _write_zip(Path(td), "fixture2.apx", {
                "apkg.json": manifest,
                "main.py": _LET_EXT_SRC.format(id="apx_with_manifest"),
            })
            ids = _resolve_ext_flags([str(path)])
            self.assertEqual(ids, frozenset({"apx_with_manifest"}))

    def test_bare_py_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "plain.py"
            path.write_text(_LET_EXT_SRC.format(id="apx_bare_py"), encoding="utf-8")
            ids = _resolve_ext_flags([str(path)])
            self.assertEqual(ids, frozenset({"apx_bare_py"}))

    def test_zero_registrations_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "empty.apx", {"plugin.py": "x = 1\n"})
            with self.assertRaises(RuntimeError):
                _resolve_ext_flags([str(path)])

    def test_ambiguous_zip_without_manifest_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "ambiguous.apx", {
                "a.py": "x = 1\n",
                "b.py": "y = 2\n",
            })
            with self.assertRaises(RuntimeError):
                _resolve_ext_flags([str(path)])

    def test_mismatched_manifest_format_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            manifest = json.dumps({"format": "asmpython.apkg", "version": 99, "kind": "extension", "entry": "main.py"})
            path = _write_zip(Path(td), "badver.apx", {
                "apkg.json": manifest,
                "main.py": _LET_EXT_SRC.format(id="apx_badver"),
            })
            with self.assertRaises(apkg.ApkgError):
                apkg.read_entry_source(path)


class ApbAplTests(unittest.TestCase):
    def test_apb_end_to_end_via_backend_registry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "fixture.apb", {"plugin.py": _DUMMY_BACKEND_SRC.format(name="apkg_test_backend")})
            resolved = _resolve_backend_flag(str(path))
            self.assertEqual(resolved, "apkg_test_backend")
            impl = get_backend(resolved)
            self.assertEqual(impl.compile(None, None), {"out": b"OBJ"})
            self.assertEqual(impl.link(None, None), {"out": b"LINKED"})

    def test_apl_end_to_end_via_linker_registry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "fixture.apl", {"plugin.py": _DUMMY_LINKER_SRC.format(name="apkg_test_linker")})
            resolved = _resolve_linker_flag(str(path))
            self.assertEqual(resolved, "apkg_test_linker")
            impl = get_linker(resolved)
            self.assertEqual(impl.link({}), b"LINKED")

    def test_bare_name_passes_through_unchanged(self) -> None:
        self.assertEqual(_resolve_backend_flag("x86-64"), "x86-64")
        self.assertEqual(_resolve_linker_flag("gcc"), "gcc")
        self.assertIsNone(_resolve_backend_flag(None))
        self.assertIsNone(_resolve_linker_flag(None))

    def test_apb_zero_registrations_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "empty.apb", {"plugin.py": "x = 1\n"})
            with self.assertRaises(RuntimeError):
                _load_backend_plugin(path)

    def test_apl_zero_registrations_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "empty.apl", {"plugin.py": "x = 1\n"})
            with self.assertRaises(RuntimeError):
                _load_linker_plugin(path)


class ApmlcTests(unittest.TestCase):
    def test_loads_single_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "fixture.apmlc", {"plugin.py": _MLANG_CONFIG_SRC})
            cfg = apkg.load_mlang_config(path)
            self.assertIsInstance(cfg, Config)
            self.assertEqual(cfg.exe, "rustc")
            self.assertEqual(cfg.frontend, "rust")
            self.assertTrue(cfg.infer_signatures)

    def test_zero_configs_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "empty.apmlc", {"plugin.py": "x = 1\n"})
            with self.assertRaises(apkg.ApkgError):
                apkg.load_mlang_config(path)

    def test_two_configs_errors(self) -> None:
        two_configs = _MLANG_CONFIG_SRC + '\nother = Config(exe="gcc", frontend="c", compile_args=["-c"])\n'
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "two.apmlc", {"plugin.py": two_configs})
            with self.assertRaises(apkg.ApkgError):
                apkg.load_mlang_config(path)


class ApmTests(unittest.TestCase):
    def test_union_registers_extension_backend_and_runs_on_load(self) -> None:
        combined = (
            _LET_EXT_SRC.format(id="apm_union_ext")
            + _DUMMY_BACKEND_SRC.format(name="apm_union_backend")
            + '\ndef on_load(am):\n    am._apm_test_flag = True\n'
        )
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "fixture.apm", {"plugin.py": combined})
            try:
                result = apkg.load_module_package(path)
                self.assertEqual(result.extension_ids, ["apm_union_ext"])
                self.assertEqual(result.backend_names, ["apm_union_backend"])
                self.assertTrue(result.ran_on_load)
                self.assertTrue(getattr(asmpython, "_apm_test_flag", False))
                # The registered extension must be immediately usable, same
                # as a directly-registered Extension.
                mod = Parser(
                    Lexer("let q = 1\nprint(q)\n").tokenize(),
                    frozenset(result.extension_ids),
                ).parse()
                self.assertTrue(any(isinstance(s, A.Assign) and s.target == "q" for s in mod.body))
            finally:
                if hasattr(asmpython, "_apm_test_flag"):
                    del asmpython._apm_test_flag

    def test_empty_package_errors(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "empty.apm", {"plugin.py": "x = 1\n"})
            with self.assertRaises(apkg.ApkgError):
                apkg.load_module_package(path)

    def test_on_load_alone_is_sufficient(self) -> None:
        src = 'def on_load(am):\n    am._apm_test_flag2 = True\n'
        with tempfile.TemporaryDirectory() as td:
            path = _write_zip(Path(td), "hookonly.apm", {"plugin.py": src})
            try:
                result = apkg.load_module_package(path)
                self.assertTrue(result.ran_on_load)
                self.assertEqual(result.extension_ids, [])
                self.assertTrue(getattr(asmpython, "_apm_test_flag2", False))
            finally:
                if hasattr(asmpython, "_apm_test_flag2"):
                    del asmpython._apm_test_flag2


if __name__ == "__main__":
    unittest.main()
