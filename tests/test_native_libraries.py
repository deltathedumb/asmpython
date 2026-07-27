"""Declaring an external native library without editing the linkers."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from asmpython._compiler.native_libraries import (  # noqa: E402
    NativeFunction,
    NativeLibrary,
    NativeLibraryError,
    NativeLibraryRegistry,
    exported_symbols,
    from_mapping,
    parse_declaration,
)

_SYSTEM32 = Path("C:/Windows/System32")
_ON_WINDOWS = sys.platform == "win32" and _SYSTEM32.is_dir()


class TestDeclaration(unittest.TestCase):
    def test_name_is_required(self):
        with self.assertRaises(NativeLibraryError):
            NativeLibrary(name="")

    def test_functions_need_a_module_to_import_them_from(self):
        with self.assertRaises(NativeLibraryError):
            NativeLibrary(name="x.dll", functions=(NativeFunction(name="f"),))

    def test_target_os_is_validated(self):
        with self.assertRaises(NativeLibraryError):
            NativeLibrary(name="x.dll", symbols=("f",), target_os="plan9")

    def test_declaring_nothing_at_all_is_an_error(self):
        # No symbols, no functions, no path: there is nothing to link against,
        # and saying so beats silently contributing an empty mapping.
        with self.assertRaises(NativeLibraryError):
            NativeLibrary(name="x.dll").resolved_symbols()

    def test_explicit_symbols_win_over_discovery(self):
        lib = NativeLibrary(name="x.dll", symbols=("only_this",), path="ignored.dll")
        self.assertEqual(lib.resolved_symbols(), ("only_this",))

    def test_declared_functions_are_always_linkable(self):
        lib = NativeLibrary(
            name="x.dll",
            symbols=("other",),
            module="x",
            functions=(NativeFunction(name="f", symbol="real_f"),),
        )
        self.assertIn("real_f", lib.resolved_symbols())

    def test_applies_to_scopes_by_target(self):
        win = NativeLibrary(name="x.dll", symbols=("f",), target_os="windows")
        self.assertTrue(win.applies_to("windows"))
        self.assertFalse(win.applies_to("linux"))
        both = NativeLibrary(name="x", symbols=("f",))
        self.assertTrue(both.applies_to("windows"))
        self.assertTrue(both.applies_to("linux"))


class TestParseDeclaration(unittest.TestCase):
    def test_bare_name_reads_a_file_of_that_name(self):
        lib = parse_declaration("SDL2.dll")
        self.assertEqual(lib.name, "SDL2.dll")
        self.assertEqual(lib.path, "SDL2.dll")

    def test_equals_form_separates_load_name_from_file(self):
        lib = parse_declaration("SDL2.dll=vendor/SDL2.dll")
        self.assertEqual(lib.name, "SDL2.dll")
        self.assertEqual(lib.path, "vendor/SDL2.dll")

    def test_colon_form_lists_symbols(self):
        lib = parse_declaration("SDL2.dll:SDL_Init, SDL_Quit")
        self.assertEqual(lib.symbols, ("SDL_Init", "SDL_Quit"))
        self.assertIsNone(lib.path)

    def test_colon_with_no_symbols_is_an_error(self):
        with self.assertRaises(NativeLibraryError):
            parse_declaration("SDL2.dll:")

    def test_empty_declaration_is_an_error(self):
        with self.assertRaises(NativeLibraryError):
            parse_declaration("   ")


class TestFromMapping(unittest.TestCase):
    def test_full_entry(self):
        lib = from_mapping({
            "name": "user32.dll",
            "target_os": "windows",
            "module": "user32",
            "functions": [
                {"name": "GetSystemMetrics", "args": ["int"], "ret": "int"},
            ],
        })
        self.assertEqual(lib.module, "user32")
        self.assertEqual(len(lib.functions), 1)
        fn = lib.functions[0]
        self.assertEqual(fn.arg_types, ("int",))
        self.assertEqual(fn.c_symbol, "GetSystemMetrics")

    def test_symbol_override_renames_the_c_side(self):
        lib = from_mapping({
            "name": "x.dll", "module": "x",
            "functions": [{"name": "nice_name", "symbol": "ugly_c_name$$1"}],
        })
        self.assertEqual(lib.functions[0].c_symbol, "ugly_c_name$$1")

    def test_missing_name_is_an_error(self):
        with self.assertRaises(NativeLibraryError):
            from_mapping({"module": "x"})

    def test_non_object_entry_is_an_error(self):
        with self.assertRaises(NativeLibraryError):
            from_mapping(["user32.dll"])


class TestRegistry(unittest.TestCase):
    def test_symbol_map_collects_declared_libraries(self):
        reg = NativeLibraryRegistry()
        reg.declare(NativeLibrary(name="a.dll", symbols=("one", "two")))
        reg.declare(NativeLibrary(name="b.dll", symbols=("three",)))
        self.assertEqual(
            reg.symbol_map("windows"),
            {"one": "a.dll", "two": "a.dll", "three": "b.dll"},
        )

    def test_builtin_symbols_are_never_retargeted(self):
        # The whole safety property: a project file cannot steal `malloc`
        # away from the C runtime.
        reg = NativeLibraryRegistry()
        reg.declare(NativeLibrary(name="evil.dll", symbols=("malloc", "mine")))
        mapping = reg.symbol_map("windows", builtin={"malloc": "msvcrt.dll"})
        self.assertNotIn("malloc", mapping)
        self.assertEqual(mapping["mine"], "evil.dll")

    def test_first_declaration_wins_a_contested_symbol(self):
        reg = NativeLibraryRegistry()
        reg.declare(NativeLibrary(name="first.dll", symbols=("shared",)))
        reg.declare(NativeLibrary(name="second.dll", symbols=("shared",)))
        self.assertEqual(reg.symbol_map("windows")["shared"], "first.dll")

    def test_target_scoping_filters_the_map(self):
        reg = NativeLibraryRegistry()
        reg.declare(NativeLibrary(name="w.dll", symbols=("f",), target_os="windows"))
        reg.declare(NativeLibrary(name="l.so", symbols=("g",), target_os="linux"))
        self.assertEqual(reg.symbol_map("windows"), {"f": "w.dll"})
        self.assertEqual(reg.symbol_map("linux"), {"g": "l.so"})

    def test_empty_registry_yields_an_empty_map(self):
        # An undeclared build must contribute nothing, which is what makes it
        # link byte-for-byte as it did before this mechanism existed.
        self.assertEqual(NativeLibraryRegistry().symbol_map("windows"), {})

    def test_refuses_to_shadow_a_stdlib_module(self):
        reg = NativeLibraryRegistry()
        reg.declare(NativeLibrary(
            name="fake.dll", module="math",
            functions=(NativeFunction(name="sqrt"),),
        ))
        with self.assertRaises(NativeLibraryError):
            reg.install_bindings(("windows",))

    def test_install_bindings_produces_ffi_funcs(self):
        from asmpython.stdlib import STDLIB_BINDINGS

        reg = NativeLibraryRegistry()
        reg.declare(NativeLibrary(
            name="demo.dll", module="_natlib_test_demo",
            functions=(NativeFunction(name="f", arg_types=("int",), ret_type="int"),),
        ))
        try:
            installed = reg.install_bindings(("windows",))
            self.assertIn("_natlib_test_demo", installed)
            func = installed["_natlib_test_demo"]["f"]
            self.assertEqual(func.arg_types, ("int",))
            self.assertEqual(func.c_name, "f")
        finally:
            STDLIB_BINDINGS.pop("_natlib_test_demo", None)

    def test_a_second_build_may_reinstall_its_own_module(self):
        """Two compiles in one process must not collide with each other.

        The stdlib-shadowing guard looks at STDLIB_BINDINGS, which the first
        install has already written to -- so without tracking what we put
        there, the second build rejects the very module it just registered.
        """
        from asmpython._compiler import native_libraries as nl
        from asmpython.stdlib import STDLIB_BINDINGS

        def build_registry():
            reg = NativeLibraryRegistry()
            reg.declare(NativeLibrary(
                name="demo.dll", module="_natlib_test_twice",
                functions=(NativeFunction(name="f"),),
            ))
            return reg

        try:
            build_registry().install_bindings(("windows",))
            build_registry().install_bindings(("windows",))   # must not raise
            self.assertIn("_natlib_test_twice", STDLIB_BINDINGS)
        finally:
            STDLIB_BINDINGS.pop("_natlib_test_twice", None)
            nl._INSTALLED_MODULES.discard("_natlib_test_twice")


@unittest.skipUnless(_ON_WINDOWS, "needs real system DLLs")
class TestPeExportReader(unittest.TestCase):
    def test_reads_kernel32_exports(self):
        syms = exported_symbols(_SYSTEM32 / "kernel32.dll")
        self.assertGreater(len(syms), 100)
        self.assertIn("GetTickCount", syms)

    def test_agrees_with_the_linkers_hardcoded_table(self):
        """Every builtin mapping must be a real export of the DLL it names.

        This validates the reader against a table that was built by hand from
        a different source, so a disagreement means one of the two is wrong --
        which is worth knowing either way.
        """
        from asmpython._backends.x86_64 import pe_linker

        cache: dict[str, set] = {}
        for symbol, dll in pe_linker._DLL_FOR_SYMBOL.items():
            path = _SYSTEM32 / dll
            if not path.is_file():
                continue
            if dll not in cache:
                cache[dll] = set(exported_symbols(path))
            self.assertIn(symbol, cache[dll], f"{symbol} is not exported by {dll}")

    def test_rejects_a_non_object_file(self):
        junk = Path(__file__)
        with self.assertRaises(NativeLibraryError):
            exported_symbols(junk)

    def test_missing_file_is_a_clear_error(self):
        lib = NativeLibrary(name="nope.dll", path="definitely_not_here.dll")
        with self.assertRaises(NativeLibraryError):
            lib.resolved_symbols()


if __name__ == "__main__":
    unittest.main()
