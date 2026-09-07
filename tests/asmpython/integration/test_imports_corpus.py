"""Multi-file programs, run two ways and compared against CPython.

`test_dynamic_python.py` does this for one file. This does it for a PROGRAM
THAT IS A DIRECTORY -- which is what every real program is, and what nothing
in this repository measured until now.

Nothing else can see this. `conformance/cases/` is 1,679 files and every one
of them is self-contained: a case is handed to `shim.run(case_path)` on its
own, and `conformance/README.md` excludes "anything requiring multiple
modules, the filesystem, sockets or subprocesses" as policy. So the 1,668-case
score and the 9,339-case `objects_diff` sweep say NOTHING WHATEVER about how
this compiler resolves an import, and both were saturated while `import a.b.c`
trapped with `NameError` and a relative import climbing past the root resolved
to a sibling.

Each entry is a directory written to `tmp_path`. `main.py` is the entry point,
and every other key is a file beside it -- a path with `/` in it makes a
package, so `{"pk/__init__.py": ...}` is written as one.

The oracle is CPython in a SUBPROCESS, not `exec`. An import reads
`sys.path[0]`, which for a script is the script's own directory, and there is
no way to get that from an in-process `exec` without editing `sys.path` for
the whole test run -- which would then leak between cases and make the oracle
depend on what ran before it. `-B` so no `__pycache__` is left behind for the
next path to find; the bytecode shapes get a fixture of their own when they
land.

## The ratchet

`KNOWN_DIVERGENT` names the entries that do NOT agree with CPython yet. It is
a ratchet in both directions: a program that starts diverging fails, and one
that starts agreeing fails until it is taken OUT of the list. The house
alternative -- recording a divergence in prose -- is the thing the goal
forbids, because a limitation written in a docstring is a limitation nobody
measures.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from io import StringIO
from pathlib import Path

from tests import harness

from asmpython.diagnostics import DiagnosticSink
from asmpython.driver import Options, compile_source
from asmpython.ir.interpreter import Interpreter

HAS_CC = shutil.which("gcc") or shutil.which("cc")

#: Entries whose answer is not CPython's yet, each with the reason.
#:
#: MEANT TO SHRINK TO EMPTY. Every name here is a divergence from CPython's
#: import resolution, which is a bug rather than a documented boundary -- see
#: this module's header for why it is a list and not a paragraph. A trap
#: counts as a divergence: the program ran and did not print what CPython
#: prints.
KNOWN_DIVERGENT: dict[str, str] = {
    "a_local_file_shadows_the_bundled_library":
        "the bundled library wins a name ahead of the source's own directory "
        "-- `imports.py:288` skips a local file outright when the name is "
        "bundled, where CPython puts the script's directory at sys.path[0]. "
        "Prints the bundled `keyword.iskeyword('for')` answer instead of the "
        "file sitting beside main.py.",
    "imported_main_guard_does_not_run":
        "every spliced module shares the program's `__name__`, which is the "
        "constant '__main__' for the whole merged module -- so an imported "
        "file's `if __name__ == \"__main__\":` block RUNS on import. The most "
        "common idiom in real .py files, executing its script body silently.",
    "module_dunder_name":
        "the same cause: a spliced module has no `__name__` of its own, so a "
        "function defined in `helpers.py` reports '__main__'.",
    "side_effects_run_at_the_import":
        "`imports.py:437` does `rewritten.body = prelude + rewritten.body`, "
        "so an imported module's top-level statements run BEFORE the "
        "importing module's first statement rather than where the import is.",
    "two_modules_whose_mangled_names_collide":
        "`bundled._mangled` is `prefix + module.replace('.', '_') + '_' + "
        "name`, so `a.b` and `a_b` mint the same symbol and the second "
        "definition wins both. Prints 'a_b' twice.",
    "dotted_import_binds_the_head":
        "`import a.b.c` keys the rewrite map on the dotted string, while the "
        "rewriter matches only a bare `ast.Name` -- so `lib.sub.deep.where()` "
        "is never rewritten and traps with NameError, no diagnostic. "
        "`analysis.py` already gets this right on the table path.",
    "module_as_a_value":
        "`import X` records a rewrite map, not a binding, so there is no "
        "run-time value named X. `print(helpers)`, `type(helpers)` and "
        "passing the module to a function all trap with NameError, and the "
        "IR shows a `gv_helpers` global allocated and never stored.",
}

#: Entries the compiler REFUSES outright, with the diagnostic it gives.
#:
#: A SEPARATE LIST BECAUSE A REFUSAL IS NOT A WRONG ANSWER. Nothing here
#: silently prints the wrong thing; each is a diagnostic a reader sees. It is
#: still a gap -- CPython compiles and runs every one of them -- and it still
#: only shrinks.
KNOWN_REFUSED: dict[str, str] = {
    "from_import_of_an_absent_member":
        "E0084, which is now the RIGHT sentence -- `module 'helpers' has no "
        "member 'absent'`, naming the file and listing what it does provide, "
        "rather than E0083 denying a module that was spliced successfully. "
        "What is left is that it arrives while compiling, where CPython "
        "raises ImportError at run time and this program catches it.",
    "guarded_optional_import":
        "E0083, and only for the half that is genuinely absent. The `try: "
        "import helpers` arm works now; `import definitely_not_here` inside a "
        "`try` is still refused at COMPILE time, where CPython raises "
        "ImportError at run time and the handler catches it. An import "
        "resolved while compiling cannot be caught by a `try` around it, "
        "which is the splice rather than anything about where the statement "
        "is written.",
    "namespace_package":
        "E0083. A directory without `__init__.py` is not importable, though "
        "its submodules resolve -- accidentally PEP-420-shaped for children "
        "and absent for the package itself.",
    "relative_import_climbing_past_the_root":
        "E0132 at COMPILE time, where CPython raises the same sentence at RUN "
        "time and the program catches it. The level arithmetic and the text "
        "are now CPython's exactly -- what is left is that an import is "
        "resolved while compiling, so a refusal cannot be caught by a `try` "
        "around the import. That is the splice, and it closes when a module "
        "gets a run-time existence.",
}


PROGRAMS: dict[str, dict[str, str]] = {

    # ── the shapes that already work, kept working ──────────────────────────
    "sibling_module": {
        "helpers.py": """
            def shout(word):
                return word.upper() + "!"
        """,
        "main.py": """
            from helpers import shout
            print(shout("hello"))
        """,
    },

    "sibling_module_attribute": {
        "helpers.py": """
            VALUE = 41

            def bump(n):
                return n + 1
        """,
        "main.py": """
            import helpers
            print(helpers.bump(helpers.VALUE))
        """,
    },

    "package_init": {
        "lib/__init__.py": """
            NAME = "lib"

            def hello():
                return "hello from " + NAME
        """,
        "main.py": """
            import lib
            print(lib.hello())
        """,
    },

    "package_submodule": {
        "lib/__init__.py": """
        """,
        "lib/util.py": """
            def double(n):
                return n * 2
        """,
        "main.py": """
            from lib.util import double
            print(double(21))
        """,
    },

    "package_reexport": {
        "lib/__init__.py": """
            from lib.util import shout
        """,
        "lib/util.py": """
            def shout(word):
                return word.upper()
        """,
        "main.py": """
            import lib
            print(lib.shout("quiet"))
        """,
    },

    "import_as": {
        "helpers.py": """
            def shout(word):
                return word.upper()
        """,
        "main.py": """
            import helpers as h
            print(h.shout("aliased"))
        """,
    },

    "two_modules_one_dependency": {
        "base.py": """
            def core():
                return "core"
        """,
        "left.py": """
            from base import core

            def go():
                return "left/" + core()
        """,
        "right.py": """
            from base import core

            def go():
                return "right/" + core()
        """,
        "main.py": """
            import left
            import right
            print(left.go())
            print(right.go())
        """,
    },

    # ── gap 8: the statement binds nothing a program can name ───────────────
    "module_as_a_value": {
        "helpers.py": """
            VALUE = 7
        """,
        "main.py": """
            import helpers
            print(type(helpers).__name__)
            print(helpers.__name__)

            def read(mod):
                return mod.VALUE

            print(read(helpers))
        """,
    },

    "dotted_import_binds_the_head": {
        "lib/__init__.py": """
        """,
        "lib/sub/__init__.py": """
        """,
        "lib/sub/deep.py": """
            def where():
                return "deep"
        """,
        "main.py": """
            import lib.sub.deep
            print(lib.sub.deep.where())
        """,
    },

    "from_import_of_an_absent_member": {
        "helpers.py": """
            def present():
                return 1
        """,
        "main.py": """
            try:
                from helpers import absent
            except ImportError as e:
                print("ImportError")
            else:
                print("imported")
        """,
    },

    "star_import": {
        "helpers.py": """
            A = 1
            B = 2
            _HIDDEN = 3

            def f():
                return "f"
        """,
        "main.py": """
            from helpers import *
            print(A, B, f())
            try:
                print(_HIDDEN)
            except NameError:
                print("underscore names are not bound")
        """,
    },

    "star_import_honours_dunder_all": {
        "helpers.py": """
            A = 1
            B = 2
            __all__ = ["A"]
        """,
        "main.py": """
            from helpers import *
            print(A)
            try:
                print(B)
            except NameError:
                print("B is not in __all__")
        """,
    },

    # ── gap 10: only at top level, only unmixed, and the side effects first ──
    "import_mixed_with_a_builtin_module": {
        "helpers.py": """
            def shout(word):
                return word.upper()
        """,
        "main.py": """
            import helpers, math
            print(helpers.shout("mixed"), math.floor(2.5))
        """,
    },

    "import_inside_a_function": {
        "helpers.py": """
            def shout(word):
                return word.upper()
        """,
        "main.py": """
            def go():
                import helpers
                return helpers.shout("late")

            print(go())
        """,
    },

    "guarded_optional_import": {
        "helpers.py": """
            NAME = "present"
        """,
        "main.py": """
            try:
                import helpers
            except ImportError:
                helpers = None
            print(helpers.NAME if helpers else "absent")

            try:
                import definitely_not_here
            except ImportError:
                print("absent")
        """,
    },

    "import_inside_an_if": {
        "helpers.py": """
            NAME = "conditional"
        """,
        "main.py": """
            if True:
                import helpers
            print(helpers.NAME)
        """,
    },

    "side_effects_run_at_the_import": {
        "noisy.py": """
            print("SIDE EFFECT")
            VALUE = 1
        """,
        "main.py": """
            print("A")
            import noisy
            print("B", noisy.VALUE)
        """,
    },

    "imported_main_guard_does_not_run": {
        "scripty.py": """
            def helper():
                return "helper"

            if __name__ == "__main__":
                print("SCRIPT BODY RAN")
        """,
        "main.py": """
            import scripty
            print(scripty.helper())
            print(__name__)
        """,
    },

    "module_dunder_name": {
        "helpers.py": """
            def who():
                return __name__
        """,
        "main.py": """
            import helpers
            print(helpers.who())
            print(__name__)
        """,
    },

    # ── gap 4: relative imports ─────────────────────────────────────────────
    "relative_import_within_a_package": {
        "pk/__init__.py": """
        """,
        "pk/util.py": """
            def shout(word):
                return word.upper()
        """,
        "pk/front.py": """
            from .util import shout

            def go():
                return shout("relative")
        """,
        "main.py": """
            from pk.front import go
            print(go())
        """,
    },

    "relative_import_climbing_past_the_root": {
        "outside.py": """
            MARK = "OUTSIDE -- CPython never reaches this"
        """,
        "pk/__init__.py": """
        """,
        "pk/over.py": """
            from .. import outside
        """,
        "main.py": """
            try:
                import pk.over
            except ImportError as e:
                print("ImportError:", e)
            else:
                print("imported, which CPython does not do")
        """,
    },

    # ── gap 5: the suffix ladder, and what a directory means ────────────────
    "a_package_beats_a_module_of_the_same_name": {
        "p.py": """
            WHICH = "module"
        """,
        "p/__init__.py": """
            WHICH = "package"
        """,
        "main.py": """
            import p
            print(p.WHICH)
        """,
    },

    "namespace_package": {
        "nspkg/mod.py": """
            def where():
                return "namespace"
        """,
        "main.py": """
            import nspkg
            print(type(nspkg).__name__)
            from nspkg.mod import where
            print(where())
        """,
    },

    # ── gap 6: whose name wins ──────────────────────────────────────────────
    "a_local_file_shadows_the_bundled_library": {
        "keyword.py": """
            def iskeyword(word):
                return "LOCAL SHADOW"
        """,
        "main.py": """
            import keyword
            print(keyword.iskeyword("for"))
        """,
    },

    # ── gap 7: the mangler ──────────────────────────────────────────────────
    "two_modules_whose_mangled_names_collide": {
        "a/__init__.py": """
        """,
        "a/b.py": """
            WHO = "a.b"
        """,
        "a_b.py": """
            WHO = "a_b"
        """,
        "main.py": """
            from a.b import WHO as dotted
            from a_b import WHO as flat
            print(dotted)
            print(flat)
        """,
    },

    "a_module_level_loop_target": {
        "tables.py": """
            NAMES = []
            for KEY in ("x", "y"):
                NAMES.append(KEY)
        """,
        "main.py": """
            import tables
            KEY = "main's own"
            print(tables.NAMES)
            print(KEY)
        """,
    },

    # ── gap 9: the annotated style, across a file boundary ──────────────────
    "an_annotated_function_from_another_module": {
        "sm.py": """
            def f() -> int:
                return 1
        """,
        "main.py": """
            from sm import f
            print(f())
        """,
    },
}


def _write(files: dict[str, str], root: Path) -> Path:
    """The program on disk. Returns `main.py`."""
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(body).strip() + "\n", encoding="utf-8")
    return root / "main.py"


def cpython(files: dict[str, str], root: Path) -> list[str]:
    """What CPython prints, from a real subprocess.

    THE ORACLE HAS TO BE A PROCESS. `sys.path[0]` for a script is the script's
    own directory, and that is the single most load-bearing fact in the whole
    of import resolution -- an in-process `exec` would have to fake it by
    editing `sys.path`, which then leaks into every later case in the run.
    """
    where = root / "oracle"
    main = _write(files, where)
    ran = subprocess.run([sys.executable, "-B", str(main)],
                         capture_output=True, text=True, encoding="utf-8",
                         cwd=str(where))
    assert ran.returncode == 0, (
        f"the oracle itself failed, so the case is wrong:\n{ran.stderr}")
    return ran.stdout.replace("\r\n", "\n").split("\n")[:-1]


def compile_it(files: dict[str, str], root: Path, optimise: bool = False):
    """The program through the frontend, or the diagnostics that refused it."""
    where = root / "subject"
    main = _write(files, where)
    sink = DiagnosticSink()
    result = compile_source(Options(source=main, optimise=optimise), sink)
    return result, sink


def _refusal(sink) -> str:
    return "; ".join(sorted({d.code or "" for d in sink.diagnostics})) or "?"


@harness.cases("name", sorted(PROGRAMS))
class TestEveryPathAgreesOnAProgramOfSeveralFiles:
    """CPython, the reference interpreter and the C backend, on one program.

    Two asmpython paths rather than four, matching `test_dynamic_python`: the
    machine backends cost an assemble and link per program and are covered by
    the differential fuzzers instead.
    """

    def _expected(self, name: str) -> str | None:
        return KNOWN_DIVERGENT.get(name) or KNOWN_REFUSED.get(name)

    def test_the_interpreter_matches_cpython(self, name, tmp_path):
        files = PROGRAMS[name]
        want = cpython(files, tmp_path)
        result, sink = compile_it(files, tmp_path)

        if not result.ok:
            assert name in KNOWN_REFUSED, (
                f"{name} is refused by the compiler and is not in "
                f"KNOWN_REFUSED: {_refusal(sink)}\n  "
                + "\n  ".join(d.message for d in sink.diagnostics))
            return
        assert name not in KNOWN_REFUSED, (
            f"{name} is in KNOWN_REFUSED and now compiles. Take it out.")

        out = StringIO()
        try:
            Interpreter(result.module, out=out).run("main")
        except Exception as exc:                       # noqa: BLE001
            assert name in KNOWN_DIVERGENT, (
                f"{name} trapped in the interpreter and is not in "
                f"KNOWN_DIVERGENT: {type(exc).__name__}: {exc}")
            return

        got = out.getvalue().split("\n")[:-1]
        if name in KNOWN_DIVERGENT:
            assert got != want, (
                f"{name} is in KNOWN_DIVERGENT and now agrees with CPython. "
                f"Take it out -- the list only shrinks.")
            return
        assert got == want

    @harness.needs("cc")
    def test_the_c_backend_matches_cpython(self, name, tmp_path):
        if self._expected(name):
            return
        from asmpython.backend import get, load_builtin
        from asmpython.target import get as get_target
        load_builtin()

        files = PROGRAMS[name]
        want = cpython(files, tmp_path)
        result, sink = compile_it(files, tmp_path)
        assert result.ok, [d.message for d in sink.diagnostics]

        c_file = tmp_path / "out.c"
        c_file.write_bytes(get("c").emit(result.module, get_target("c"))["out.c"])
        exe = tmp_path / "out.exe"
        built = subprocess.run([HAS_CC, str(c_file), "-o", str(exe)],
                               capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        ran = subprocess.run([str(exe)], capture_output=True, text=True,
                             encoding="utf-8")
        assert ran.stdout.replace("\r\n", "\n").split("\n")[:-1] == want


class TestTheRatchetIsHonest:
    """The two lists describe THIS corpus and nothing else."""

    def test_every_named_entry_exists(self):
        stray = sorted((set(KNOWN_DIVERGENT) | set(KNOWN_REFUSED))
                       - set(PROGRAMS))
        assert not stray, (
            f"named in a ratchet but not a program any more: {stray}")

    def test_nothing_is_in_both_lists(self):
        both = sorted(set(KNOWN_DIVERGENT) & set(KNOWN_REFUSED))
        assert not both, (
            f"a program is either refused or wrong, not both: {both}")

    def test_every_entry_has_a_reason(self):
        blank = sorted(name for name, why
                       in {**KNOWN_DIVERGENT, **KNOWN_REFUSED}.items()
                       if not why.strip())
        assert not blank, f"entries with no reason recorded: {blank}"
