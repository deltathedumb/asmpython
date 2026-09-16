"""The C frontend: what it accepts, what it refuses, and how it refuses.

THE CENTRAL TEST HERE IS `TestNothingCrashes`, for the reason the Python
frontend's own file gives: parsing and lowering are two passes with a contract
between them -- lowering may assume the parser accepted everything it sees --
and the failure mode when that contract breaks is not a wrong answer but a
traceback with a compiler stack in it. So a corpus of C, valid and not, is fed
at the whole pipeline and the only assertion is that a compiler behaves like
one: a module or a diagnostic, never an exception.

THE LAYOUT TESTS ASK THE HOST COMPILER. A struct's size and its members'
offsets are an ABI, not an opinion, and the only way to know this frontend
agrees with the platform is to compile the same declarations with `cc` and
compare the numbers. They are guarded on `cc` and reported as blocked where
there is none, rather than quietly passing.
"""
from __future__ import annotations

import subprocess

from tests import harness

from asmpython.diagnostics import DiagnosticSink, SourceFile
from asmpython.frontends import c as c_frontend
from asmpython.frontends.c import ctype as C
from asmpython.frontends.c.parser import parse
from asmpython.frontends.c.preprocess import Preprocessor
from asmpython.ir import verify


def compile_c(source: str, name: str = "t.c"):
    """The module, and every diagnostic. Neither may be an exception."""
    # A FRESH FRONTEND EVERY TIME, which is what `Frontend.configure`
    # returning a new instance is for: one shared object carrying a run's
    # flags would leak them into the next compilation in the same process.
    sink = DiagnosticSink()
    module = c_frontend.CFrontend().compile(SourceFile(source, name), sink)
    return module, sink


def codes(source: str) -> list[str]:
    _, sink = compile_c(source)
    return [d.code for d in sink.diagnostics]


def parse_only(source: str):
    sink = DiagnosticSink()
    tokens = Preprocessor(sink).run(SourceFile(source, "t.c"))
    unit, parser = parse(tokens, sink)
    return unit, parser, sink


# ── the type system ─────────────────────────────────────────────────────────
class TestTypes:
    def test_declarators_spiral(self):
        unit, _, sink = parse_only(
            "int (*a[3])(void); int *b[4]; int (*c)[5]; char *(*d)(int, ...);")
        assert not sink.failed, [d.message for d in sink.diagnostics]
        got = {d.name: C.spell(d.type, d.name) for d in unit.decls}
        assert got["a"] == "int (*a[3])(void)"
        assert got["b"] == "int *b[4]"
        assert got["c"] == "int (*c)[5]"
        assert got["d"] == "char *(*d)(int, ...)"

    @harness.cases("a,b,want", [
        ("int", "unsigned int", "unsigned int"),
        ("long", "unsigned int", "long"),
        ("int", "double", "double"),
        ("char", "short", "int"),
        ("unsigned long", "long", "unsigned long"),
        ("float", "long double", "long double"),
    ])
    def test_the_usual_arithmetic_conversions(self, a, b, want):
        """6.3.1.8. `long` beats `unsigned int` because it is strictly wider;
        `unsigned long` beats `long` because it is not."""
        table = {"int": C.INT, "unsigned int": C.UINT, "long": C.LONG,
                 "unsigned long": C.ULONG, "double": C.DOUBLE,
                 "char": C.CHAR, "short": C.SHORT, "float": C.FLOAT,
                 "long double": C.LDOUBLE}
        assert C.spell(C.usual_arithmetic(table[a], table[b])) == want

    def test_a_bitfields_promotion_depends_on_its_width(self):
        """`unsigned x : 3` promotes to `int`, because every value fits."""
        assert C.promote_bitfield(C.UINT, 3) is C.INT
        assert C.promote_bitfield(C.UINT, 32) is C.UINT

    def test_an_array_decays_carrying_the_qualifier(self):
        """`const int a[4]` is an array of `const int`, so `a` is
        `const int *` -- the qualifier is written on the array and belongs on
        the element."""
        ty = C.array_of(C.INT, 4).qualified({"const"})
        assert C.spell(C.decay(ty)) == "const int *"


# ── layout, against the host compiler ───────────────────────────────────────
LAYOUT_TYPES = [
    "struct { char c; int i; }",
    "struct { char c; double d; int i; }",
    "struct { int a; char b; char c; }",
    "struct { double d; char c; }",
    "union { int i; char b[7]; double d; }",
    "struct { unsigned a : 3; unsigned b : 5; }",
    "struct { unsigned a : 3; unsigned b : 30; }",
    "struct { unsigned a : 3; int : 0; unsigned b : 1; }",
    "struct { char c; unsigned a : 12; char d; }",
    "struct { short s; struct { char a; int b; } inner; char t; }",
    "struct { char a[3]; short b; }",
    "struct { long long a; char b; }",
]


def _host_layout(decl: str) -> tuple[int, int]:
    program = (f'#include <stdio.h>\ntypedef {decl} T;\n'
               'int main(void){ printf("%zu %zu\\n", sizeof(T), _Alignof(T)); '
               'return 0; }\n')
    import pathlib
    import tempfile
    d = tempfile.mkdtemp()
    src = pathlib.Path(d) / "a.c"
    exe = pathlib.Path(d) / "a.out"
    src.write_text(program, encoding="utf-8")
    build = subprocess.run(["cc", "-std=c11", "-w", "-o", str(exe), str(src)],
                           capture_output=True, text=True)
    if build.returncode:
        harness.skip(f"host cc refused the declaration: {build.stderr[:120]}")
    out = subprocess.run([str(exe)], capture_output=True, text=True).stdout
    size, align = out.split()
    return int(size), int(align)


class TestLayoutMatchesTheHost:
    """A struct's size is an ABI. See the module docstring."""

    @harness.needs("cc")
    @harness.cases("decl", LAYOUT_TYPES)
    def test_size_and_alignment(self, decl):
        unit, _, sink = parse_only(f"typedef {decl} T; T probe;")
        assert not sink.failed, [d.message for d in sink.diagnostics]
        ours = unit.decls[-1].type
        assert (ours.size, ours.align) == _host_layout(decl)


# ── diagnostics ─────────────────────────────────────────────────────────────
class TestItRefusesWithAReason:
    @harness.cases("source,code", [
        ("int f(void){ return undeclared; }", "E1207"),
        ("int f(void){ return g(1); }", "E1206"),
        ("struct s { int x; }; int f(struct s a){ return a.y; }", "E1432"),
        ("int f(void){ const int x = 1; x = 2; return x; }", "E1407"),
        ('int f(void){ return "abc" * 2; }', "E1416"),
        ("int f(void){ int a[2]; a = a; return 0; }", "E1406"),
        ("struct s; int f(struct s v){ return 0; }", "E1282"),
        ("int f(void){ return sizeof(struct nope); }", "E1203"),
        ("void f(void); int g(void){ return f(); }", "E1401"),
        ("int f(int a, int a){ return a; }", "E1276"),
        ("int f(void){ switch (1) { case 1: case 1: ; } return 0; }", "E1259"),
        ("int f(void){ goto nowhere; }", "E1283"),
        ("_Static_assert(1 == 2, \"no\");", "E1267"),
        ("int f(void){ return _Generic(1.0f, int: 1); }", "E1211"),
        ("struct s { int b; int a[]; int c; };", "E1222"),
        ("struct s { int a[]; };", "E1228"),
        ("int f(void){ break; return 0; }", "E1252"),
        ("int x = 1; int x = 2;", "E1276"),
        ("int f(void){ struct s { int x; }; struct s v; return v.x + v.x.y; }",
         "E1430"),
    ])
    def test_the_code_is_the_one_documented(self, source, code):
        assert code in codes(source), codes(source)

    def test_a_misspelled_member_suggests_the_right_one(self):
        _, sink = compile_c("struct s { int count; };\n"
                            "int f(struct s *p){ return p->cout; }")
        helps = [h for d in sink.diagnostics for h in d.helps]
        assert any("count" in h for h in helps), helps

    def test_a_signed_unsigned_comparison_is_warned_about(self):
        """C's most common invisible bug: the signed side becomes unsigned,
        so a negative value compares greater than a positive one."""
        assert "W1422" in codes(
            "int f(int i, unsigned n){ return i < n; }")

    def test_an_unsupported_construct_says_so(self):
        assert "E1214" in codes("_Complex double z;")

    def test_every_error_has_a_position(self):
        _, sink = compile_c("int f(void){ return nope; }")
        assert sink.diagnostics
        assert all(d.has_location for d in sink.diagnostics)


# ── the contract between the parser and lowering ────────────────────────────
CORPUS = [
    "int main(void){ return 0; }",
    "int main(void){ int a[3] = {1,2,3}; return a[2]; }",
    "struct s { int x, y; }; int main(void){ struct s v = {1,2}; return v.x; }",
    "int main(void){ for (int i = 0; i < 3; i++) ; return 0; }",
    "int main(void){ switch (1) { default: break; } return 0; }",
    "int main(void){ int i = 0; do { i++; } while (i < 3); return i; }",
    "int f(int n){ return n ? f(n-1) : 0; } int main(void){ return f(3); }",
    "int main(void){ int n = 4; int a[n]; return a[0]; }",
    "int main(void){ return (int){5}; }",
    "int main(void){ return _Generic(1, int: 1, default: 0); }",
    "int main(void){ return ({ int x = 2; x * 3; }); }",
    "int main(void){ int x = 0; x += 1; x <<= 2; x %= 3; return x; }",
    "union u { int i; char c[4]; }; int main(void){ union u v; v.i = 1; return v.c[0]; }",
    "struct b { unsigned a : 3; int s : 4; }; int main(void){ struct b v = {0}; return v.a; }",
    "int main(void){ char *p = \"hi\"; return p[0]; }",
    "int main(void){ double d = 1.5; return (int)d; }",
    "int main(int argc, char **argv){ return argc; }",
    "static int x; int main(void){ return x; }",
    "int main(void){ goto end; end: return 0; }",
    "int (*f)(void); int main(void){ return f ? f() : 0; }",
    # ── and the same shapes, wrong ─────────────────────────────────────────
    "int main(void){ return; }",
    "int main(void){ return 1 + ; }",
    "struct { int x } y;",
    "int main(void){ int a[-1]; return 0; }",
    "int main(void){ return *1; }",
    "int f(void) { }",
    "int main(void){ struct nope v; return 0; }",
    "int main(void){ return 1 / 0; }",
    "int main(void){ case 1: return 0; }",
    "int main(void){ int x = \"str\"; return x; }",
    "void f(void){ return 1; }",
    "int main(void){ f(1,2,3); }",
    "#define M(a) a\nint main(void){ return M(M(M(1))); }",
    "int main(void){ return sizeof(void); }",
    "typedef int T; int main(void){ T T; return 0; }",
    # ── malformed struct bodies, because a parser loop that consumes
    # nothing does not fail, it HANGS -- and a compiler that hangs is the
    # one failure a user cannot diagnose. A mis-indented `return` in
    # `struct_or_union` made `struct s { struct s *p; };` do exactly that.
    "struct s { struct s *next; }; int main(void){ return 0; }",
    "struct s { int; };",
    "struct s { int x };",
    "struct s { ; };",
    "struct s { int x; ) };",
    "union u { struct u *p; };",
    "struct s { struct { struct s *q; } in; };",
]


class TestNothingCrashes:
    """A result or a diagnostic, never an exception. See the module docstring."""

    @harness.cases("source", [harness.param(s, id=f"case{i}")
                              for i, s in enumerate(CORPUS)])
    def test_the_compiler_behaves_like_a_compiler(self, source):
        module, sink = compile_c(source)
        if module is not None:
            assert not sink.failed
            # AND THE IR IT PRODUCED IS VALID. A frontend that returns a
            # module the verifier rejects has failed just as surely as one
            # that raises -- the user sees an internal error either way.
            verify(module)
        else:
            assert sink.failed, "returned None without reporting anything"


#: Every header C23 requires. The two that refuse do so with `#error` and a
#: reason; the rest have to compile on their own, because a header that only
#: works when something else was included first is a trap.
C23_HEADERS = [
    "assert.h", "complex.h", "ctype.h", "errno.h", "fenv.h", "float.h",
    "inttypes.h", "iso646.h", "limits.h", "locale.h", "math.h", "setjmp.h",
    "signal.h", "stdalign.h", "stdarg.h", "stdatomic.h", "stdbit.h",
    "stdbool.h", "stdckdint.h", "stddef.h", "stdint.h", "stdio.h", "stdlib.h",
    "stdnoreturn.h", "string.h", "tgmath.h", "threads.h", "time.h", "uchar.h",
    "wchar.h", "wctype.h",
]

#: The three that cannot exist here, and the diagnostic each one gives. A
#: refusal with a reason is a feature; a missing file is a mystery.
REFUSING = {"complex.h", "setjmp.h", "threads.h"}


class TestItAlwaysTerminates:
    """A parser loop bounded by a closing token must consume something.

    Every one of these reached the end at some point only because a loop
    spun: the test is that the call RETURNS, and the harness's own timeout is
    what fails it if the guard is ever removed.
    """

    @harness.cases("source", [
        "struct s { struct s *next; };",
        "struct s { int x",
        "union u { int",
        "enum e { A, ",
        "struct s { int x; struct s y; };",
        "int f(void) { struct q { struct q *p; } v; return 0; }",
    ])
    def test_a_malformed_aggregate_still_finishes(self, source):
        module, sink = compile_c(source)
        assert module is not None or sink.failed


class TestTheStandardHeaders:
    @harness.cases("name", C23_HEADERS)
    def test_each_one_is_there_and_stands_alone(self, name):
        module, sink = compile_c(f"#include <{name}>\nint main(void){{return 0;}}")
        codes_ = [d.code for d in sink.diagnostics]
        if name in REFUSING:
            assert codes_ == ["E1112"], codes_
            assert len(sink.diagnostics[0].message) > 40, "the reason is missing"
            return
        assert module is not None, codes_
        assert not sink.failed, codes_

    def test_all_of_them_at_once(self):
        """Including every header a program could include, together. A macro
        one of them defines can break the next; the only way to know is to
        put them all in one translation unit."""
        includes = "".join(f"#include <{h}>\n"
                           for h in C23_HEADERS if h not in REFUSING)
        module, sink = compile_c(includes + "int main(void){ return 0; }")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]


class TestTheFourDivergences:
    """`frontends/c/__init__.py` names four places this frontend differs from
    a hosted implementation, and calls that list complete. A list nothing
    checks becomes a list of the ones somebody remembered."""

    def test_long_double_is_double(self):
        assert C.LDOUBLE.size == C.DOUBLE.size
        module, sink = compile_c(
            "#include <float.h>\n"
            "_Static_assert(sizeof(long double) == 8, \"\");\n"
            "_Static_assert(LDBL_MAX == DBL_MAX, \"\");\n")
        assert not sink.failed, [d.message for d in sink.diagnostics]

    def test_complex_is_refused_with_a_reason(self):
        _, sink = compile_c("_Complex double z;")
        assert [d.code for d in sink.diagnostics] == ["E1214"]
        assert "complex" in sink.diagnostics[0].notes[0]

    def test_setjmp_says_why_rather_than_going_missing(self):
        """The header exists so the error names the problem instead of
        `cannot find include file 'setjmp.h'`."""
        _, sink = compile_c("#include <setjmp.h>\nint main(void){return 0;}")
        assert [d.code for d in sink.diagnostics] == ["E1112"]
        assert "machine frame" in sink.diagnostics[0].message

    def test_main_with_parameters_is_warned_about(self):
        _, sink = compile_c("int main(int argc, char **argv){ return argc; }")
        assert [d.code for d in sink.diagnostics] == ["W1501"]


class TestTheModuleItProduces:
    def test_the_entry_point_is_a_wrapper(self):
        """Every backend's runtime declares `int64_t main(void)`, so a C
        `main` returning `int` cannot be it."""
        module, _ = compile_c("int main(void){ return 3; }")
        names = {f.name for f in module.functions}
        assert "main" in names and "__c_main" in names
        assert str(module.function("main").ret) == "i64"
        assert module.function("main").params == []

    def test_an_unused_library_function_is_dropped(self):
        module, _ = compile_c(
            "#include <stdio.h>\n#include <stdlib.h>\n"
            "int main(void){ return 0; }")
        names = {f.name for f in module.functions}
        assert "qsort" not in names
        assert "printf" not in names

    def test_a_string_literal_becomes_a_readonly_global(self):
        module, _ = compile_c('char *p; int main(void){ p = "hi"; return 0; }')
        strings = [g for g in module.globals if g.data == b"hi\x00"]
        assert strings and strings[0].readonly

    def test_a_static_initialiser_needing_an_address_runs_before_main(self):
        """`Global.data` is bytes and holds no relocations, so the store goes
        into a generated `__c_init` that the entry point calls first."""
        module, _ = compile_c(
            "int v = 7; int *p = &v; int main(void){ return *p; }")
        assert module.function("__c_init") is not None
        entry = module.function("main")
        calls = [i.sym for _, i in entry.instructions() if i.sym]
        assert calls[0] == "__c_init"

    def test_a_struct_is_returned_through_a_hidden_parameter(self):
        module, _ = compile_c(
            "struct p { int a, b; };\n"
            "struct p make(int n){ struct p r = {n, n+1}; return r; }\n"
            "int main(void){ return make(1).b; }")
        make = module.function("make")
        assert str(make.ret) == "void"
        assert str(make.registers[make.params[0]]) == "ptr"
