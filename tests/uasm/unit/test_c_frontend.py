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

from uasm.diagnostics import DiagnosticSink, SourceFile
from uasm.frontends import c as c_frontend
from uasm.frontends.c import ctype as C
from uasm.frontends.c.parser import parse
from uasm.frontends.c.preprocess import Preprocessor
from uasm.ir import verify


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

    @harness.cases("source,code", [
        # `restrict` IS A PROMISE ABOUT A POINTER and means nothing else, so
        # C makes it a constraint violation rather than a no-op.
        ("int restrict x;", "E1218"),
        ("int f(double restrict d){ return (int)d; }", "E1218"),
        # A BIT-FIELD HAS NO ADDRESS for an alignment to be a property of.
        ("struct s { _Alignas(8) int b : 3; };", "E1225"),
        # `void` HAS NO SIZE, and gcc's extension answering 1 is only worth
        # having to make `void *` arithmetic work -- which this refuses, as
        # C says to. Half an extension would be an answer with nothing to do.
        ("int n = sizeof(void);", "E1203"),
        ("int n = _Alignof(void);", "E1203"),
        ("void f(void a[3]);", "E1238"),
        # A BIT-FIELD IS PART OF AN OBJECT and not one of its own.
        ("struct s{int b:3;}; int n(struct s v){ return (int)sizeof v.b; }",
         "E1226"),
        # AND THE TWO SPELLINGS OF ONE OPERATOR AGREE. The postfix form had
        # its own copy of the checks, missing two of them.
        ("void f(void *p){ p++; }", "E1413"),
        ("void f(void){ _Complex double z = 0; z++; }", "E1414"),
        # 6.10.9.3: the seven predefined names and `defined`.
        ("#define __LINE__ 5", "E1124"),
        ("#undef __STDC_VERSION__", "E1124"),
        ("#undef defined", "E1123"),
    ])
    def test_a_constraint_violation_with_no_sensible_reading(self, source,
                                                             code):
        got = codes(source)
        assert code in got, got

    @harness.cases("source,code", [
        # THE THREE GNU EXTENSIONS THIS FRONTEND KEEPS, each a constraint
        # violation C requires a diagnostic for -- and a warning is one. The
        # program still compiles, which is the whole point of keeping them:
        # real headers use the struct hack, and `sizeof(void)` is 1 so that
        # `p + 1` on a `void *` means what everybody writes it to mean.
        ("int a[0];", "W1236"),
        ("struct s { };", "W1221"),
        ("union u { };", "W1221"),
        # And these C asks for outright.
        ("int a[2] = {1, 2, 3};", "W1243"),
        # UNDEFINED RATHER THAN A VIOLATION, so C asks for nothing -- but a
        # constant that can only be wrong is worth a word.
        ("int f(int x){ return x / 0; }", "W1417"),
        ("int f(int x){ return x % 0; }", "W1417"),
        ("int f(int x){ return x << -1; }", "W1418"),
        ("int f(int x){ return x << 40; }", "W1418"),
    ])
    def test_an_extension_is_diagnosed_and_still_compiles(self, source, code):
        module, sink = compile_c(source + "\nint main(void){ return 0; }\n")
        got = [d.code for d in sink.diagnostics]
        assert code in got, got
        assert module is not None and not sink.failed, got

    @harness.cases("source", [
        "int *restrict p;",
        "int f(int a[restrict 4]){ return a[0]; }",
        "int f(char *restrict a, const char *restrict b){ return *a + *b; }",
        "struct s { _Alignas(8) int b; };",
        "struct s { int a; };",
        "int a[3];",
        "int n = sizeof(int);",
        "int a[2] = {1, 2};",
        "int f(int x){ return x << 3; }",
        "long f(long x){ return x << 40; }",
        "int f(int x, int y){ return x / y; }",
        "int f(int *p){ return *p++; }",
        "struct s{int b;}; int n(struct s v){ return (int)sizeof v.b; }",
        "#define Q 1\n#undef Q\nint q = 1;",
        "#define __STDC_WANT_LIB_EXT1__ 1\nint q = 1;",
    ])
    def test_and_the_valid_spellings_stay_quiet(self, source):
        module, sink = compile_c(source + "\nint main(void){ return 0; }\n")
        got = [(d.code, d.message) for d in sink.diagnostics]
        assert got == [], got
        assert module is not None

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
        assert "E1214" in codes("_Imaginary double z;")

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
REFUSING: set[str] = set()


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


#: Every macro C23 requires a header to define. NOT `NDEBUG`, which the
#: PROGRAM defines and the header only reads; not `FP_FAST_FMA` and its
#: two friends, which are defined only where `fma` is FASTER than the
#: multiply and add written out and this one is exactly that; and not
#: `thread_local`, which C23 made a keyword, so a header need not and
#: `#ifndef` could not see it.
C23_MACROS: dict[str, str] = {
    "assert.h": """
        __STDC_VERSION_ASSERT_H__ assert static_assert
    """,
    "complex.h": """
        CMPLX CMPLXF CMPLXL I _Complex_I __STDC_VERSION_COMPLEX_H__
        complex
    """,
    "errno.h": """
        EDOM EILSEQ ERANGE __STDC_VERSION_ERRNO_H__ errno
    """,
    "fenv.h": """
        FE_ALL_EXCEPT FE_DFL_ENV FE_DIVBYZERO FE_INEXACT FE_INVALID
        FE_OVERFLOW FE_UNDERFLOW __STDC_VERSION_FENV_H__
    """,
    "float.h": """
        DBL_DECIMAL_DIG DBL_DIG DBL_EPSILON DBL_HAS_SUBNORM
        DBL_IS_IEC_60559 DBL_MANT_DIG DBL_MAX DBL_MAX_10_EXP DBL_MAX_EXP
        DBL_MIN DBL_MIN_10_EXP DBL_MIN_EXP DBL_NORM_MAX DBL_TRUE_MIN
        DECIMAL_DIG FLT_DECIMAL_DIG FLT_DIG FLT_EPSILON FLT_EVAL_METHOD
        FLT_HAS_SUBNORM FLT_IS_IEC_60559 FLT_MANT_DIG FLT_MAX
        FLT_MAX_10_EXP FLT_MAX_EXP FLT_MIN FLT_MIN_10_EXP FLT_MIN_EXP
        FLT_NORM_MAX FLT_RADIX FLT_ROUNDS FLT_TRUE_MIN INFINITY
        LDBL_DECIMAL_DIG LDBL_DIG LDBL_EPSILON LDBL_HAS_SUBNORM
        LDBL_IS_IEC_60559 LDBL_MANT_DIG LDBL_MAX LDBL_MAX_10_EXP
        LDBL_MAX_EXP LDBL_MIN LDBL_MIN_10_EXP LDBL_MIN_EXP LDBL_NORM_MAX
        LDBL_TRUE_MIN NAN __STDC_VERSION_FLOAT_H__
    """,
    # ALL TWO HUNDRED, because the header's table is generated and a
    # generated table is exactly the kind with a hole in one corner. Eight
    # conversions for `printf`, six for `scanf`, fourteen widths each.
    "inttypes.h": """
       
        __STDC_VERSION_INTTYPES_H__ PRId8 PRId16 PRId32 PRId64 PRIdLEAST8
        PRIdLEAST16 PRIdLEAST32 PRIdLEAST64 PRIdFAST8 PRIdFAST16
        PRIdFAST32 PRIdFAST64 PRIdMAX PRIdPTR PRIi8 PRIi16 PRIi32 PRIi64
        PRIiLEAST8 PRIiLEAST16 PRIiLEAST32 PRIiLEAST64 PRIiFAST8
        PRIiFAST16 PRIiFAST32 PRIiFAST64 PRIiMAX PRIiPTR PRIo8 PRIo16
        PRIo32 PRIo64 PRIoLEAST8 PRIoLEAST16 PRIoLEAST32 PRIoLEAST64
        PRIoFAST8 PRIoFAST16 PRIoFAST32 PRIoFAST64 PRIoMAX PRIoPTR PRIu8
        PRIu16 PRIu32 PRIu64 PRIuLEAST8 PRIuLEAST16 PRIuLEAST32
        PRIuLEAST64 PRIuFAST8 PRIuFAST16 PRIuFAST32 PRIuFAST64 PRIuMAX
        PRIuPTR PRIx8 PRIx16 PRIx32 PRIx64 PRIxLEAST8 PRIxLEAST16
        PRIxLEAST32 PRIxLEAST64 PRIxFAST8 PRIxFAST16 PRIxFAST32 PRIxFAST64
        PRIxMAX PRIxPTR PRIX8 PRIX16 PRIX32 PRIX64 PRIXLEAST8 PRIXLEAST16
        PRIXLEAST32 PRIXLEAST64 PRIXFAST8 PRIXFAST16 PRIXFAST32 PRIXFAST64
        PRIXMAX PRIXPTR PRIb8 PRIb16 PRIb32 PRIb64 PRIbLEAST8 PRIbLEAST16
        PRIbLEAST32 PRIbLEAST64 PRIbFAST8 PRIbFAST16 PRIbFAST32 PRIbFAST64
        PRIbMAX PRIbPTR PRIB8 PRIB16 PRIB32 PRIB64 PRIBLEAST8 PRIBLEAST16
        PRIBLEAST32 PRIBLEAST64 PRIBFAST8 PRIBFAST16 PRIBFAST32 PRIBFAST64
        PRIBMAX PRIBPTR SCNd8 SCNd16 SCNd32 SCNd64 SCNdLEAST8 SCNdLEAST16
        SCNdLEAST32 SCNdLEAST64 SCNdFAST8 SCNdFAST16 SCNdFAST32 SCNdFAST64
        SCNdMAX SCNdPTR SCNi8 SCNi16 SCNi32 SCNi64 SCNiLEAST8 SCNiLEAST16
        SCNiLEAST32 SCNiLEAST64 SCNiFAST8 SCNiFAST16 SCNiFAST32 SCNiFAST64
        SCNiMAX SCNiPTR SCNo8 SCNo16 SCNo32 SCNo64 SCNoLEAST8 SCNoLEAST16
        SCNoLEAST32 SCNoLEAST64 SCNoFAST8 SCNoFAST16 SCNoFAST32 SCNoFAST64
        SCNoMAX SCNoPTR SCNu8 SCNu16 SCNu32 SCNu64 SCNuLEAST8 SCNuLEAST16
        SCNuLEAST32 SCNuLEAST64 SCNuFAST8 SCNuFAST16 SCNuFAST32 SCNuFAST64
        SCNuMAX SCNuPTR SCNx8 SCNx16 SCNx32 SCNx64 SCNxLEAST8 SCNxLEAST16
        SCNxLEAST32 SCNxLEAST64 SCNxFAST8 SCNxFAST16 SCNxFAST32 SCNxFAST64
        SCNxMAX SCNxPTR SCNb8 SCNb16 SCNb32 SCNb64 SCNbLEAST8 SCNbLEAST16
        SCNbLEAST32 SCNbLEAST64 SCNbFAST8 SCNbFAST16 SCNbFAST32 SCNbFAST64
        SCNbMAX SCNbPTR
    """,
    "iso646.h": """
        __STDC_VERSION_ISO646_H__ and and_eq bitand bitor compl not not_eq
        or or_eq xor xor_eq
    """,
    "limits.h": """
        BITINT_MAXWIDTH BOOL_MAX BOOL_WIDTH CHAR_BIT CHAR_MAX CHAR_MIN
        CHAR_WIDTH INT_MAX INT_MIN INT_WIDTH LLONG_MAX LLONG_MIN
        LLONG_WIDTH LONG_MAX LONG_MIN LONG_WIDTH MB_LEN_MAX SCHAR_MAX
        SCHAR_MIN SCHAR_WIDTH SHRT_MAX SHRT_MIN SHRT_WIDTH UCHAR_MAX
        UCHAR_WIDTH UINT_MAX UINT_WIDTH ULLONG_MAX ULLONG_WIDTH ULONG_MAX
        ULONG_WIDTH USHRT_MAX USHRT_WIDTH __STDC_VERSION_LIMITS_H__
    """,
    "locale.h": """
        LC_ALL LC_COLLATE LC_CTYPE LC_MONETARY LC_NUMERIC LC_TIME NULL
        __STDC_VERSION_LOCALE_H__
    """,
    "math.h": """
        FP_ILOGB0 FP_ILOGBNAN FP_INFINITE FP_NAN FP_NORMAL FP_SUBNORMAL
        FP_ZERO HUGE_VAL HUGE_VALF HUGE_VALL INFINITY MATH_ERREXCEPT
        MATH_ERRNO NAN __STDC_VERSION_MATH_H__ fpclassify isfinite
        isgreater isgreaterequal isinf isless islessequal islessgreater
        isnan isnormal isunordered math_errhandling signbit
    """,
    "setjmp.h": """
        __STDC_VERSION_SETJMP_H__ setjmp
    """,
    "signal.h": """
        SIGABRT SIGFPE SIGILL SIGINT SIGSEGV SIGTERM SIG_DFL SIG_ERR
        SIG_IGN __STDC_VERSION_SIGNAL_H__
    """,
    "stdalign.h": """
        __STDC_VERSION_STDALIGN_H__ __alignas_is_defined
        __alignof_is_defined alignas alignof
    """,
    "stdarg.h": """
        __STDC_VERSION_STDARG_H__ va_arg va_copy va_end va_start
    """,
    "stdatomic.h": """
        ATOMIC_BOOL_LOCK_FREE ATOMIC_CHAR_LOCK_FREE ATOMIC_FLAG_INIT
        ATOMIC_INT_LOCK_FREE ATOMIC_LLONG_LOCK_FREE ATOMIC_LONG_LOCK_FREE
        ATOMIC_POINTER_LOCK_FREE ATOMIC_SHORT_LOCK_FREE ATOMIC_VAR_INIT
        __STDC_VERSION_STDATOMIC_H__ atomic_compare_exchange_strong
        atomic_exchange atomic_fetch_add atomic_init atomic_is_lock_free
        atomic_load atomic_store kill_dependency
    """,
    "stdbit.h": """
        __STDC_ENDIAN_BIG__ __STDC_ENDIAN_LITTLE__ __STDC_ENDIAN_NATIVE__
        __STDC_VERSION_STDBIT_H__
    """,
    "stdbool.h": """
        __STDC_VERSION_STDBOOL_H__ __bool_true_false_are_defined bool
        false true
    """,
    "stdckdint.h": """
        __STDC_VERSION_STDCKDINT_H__ ckd_add ckd_mul ckd_sub
    """,
    "stddef.h": """
        NULL __STDC_VERSION_STDDEF_H__ offsetof unreachable
    """,
    "stdint.h": """
        INT16_MAX INT16_MIN INT32_MAX INT32_MIN INT64_C INT64_MAX
        INT64_MIN INT8_C INT8_MAX INT8_MIN INT8_WIDTH INTMAX_C INTMAX_MAX
        INTMAX_MIN INTMAX_WIDTH INTPTR_MAX INTPTR_MIN INTPTR_WIDTH
        INT_FAST8_MAX INT_FAST8_MIN INT_FAST8_WIDTH INT_LEAST8_MAX
        INT_LEAST8_MIN INT_LEAST8_WIDTH PTRDIFF_MAX PTRDIFF_MIN
        PTRDIFF_WIDTH SIG_ATOMIC_MAX SIG_ATOMIC_MIN SIG_ATOMIC_WIDTH
        SIZE_MAX SIZE_WIDTH UINT16_MAX UINT32_MAX UINT64_C UINT64_MAX
        UINT8_C UINT8_MAX UINT8_WIDTH UINTMAX_C UINTMAX_MAX UINTPTR_MAX
        UINT_FAST8_MAX UINT_LEAST8_MAX WCHAR_MAX WCHAR_MIN WCHAR_WIDTH
        WINT_MAX WINT_MIN WINT_WIDTH __STDC_VERSION_STDINT_H__
    """,
    "stdio.h": """
        BUFSIZ EOF FILENAME_MAX FOPEN_MAX L_tmpnam NULL SEEK_CUR SEEK_END
        SEEK_SET TMP_MAX _IOFBF _IOLBF _IONBF __STDC_VERSION_STDIO_H__
        stderr stdin stdout
    """,
    "stdlib.h": """
        EXIT_FAILURE EXIT_SUCCESS MB_CUR_MAX NULL RAND_MAX
        __STDC_VERSION_STDLIB_H__
    """,
    "stdnoreturn.h": """
        __STDC_VERSION_STDNORETURN_H__ noreturn
    """,
    "string.h": """
        NULL __STDC_VERSION_STRING_H__
    """,
    "tgmath.h": """
        __STDC_VERSION_TGMATH_H__
    """,
    "threads.h": """
        ONCE_FLAG_INIT TSS_DTOR_ITERATIONS __STDC_VERSION_THREADS_H__
    """,
    "time.h": """
        CLOCKS_PER_SEC NULL TIME_MONOTONIC TIME_UTC
        __STDC_VERSION_TIME_H__
    """,
    "uchar.h": """
        __STDC_VERSION_UCHAR_H__
    """,
    "wchar.h": """
        NULL WCHAR_MAX WCHAR_MIN WEOF __STDC_VERSION_WCHAR_H__
    """,
    "wctype.h": """
        WEOF __STDC_VERSION_WCTYPE_H__
    """,
}


#: Every function C23 requires a header to declare, by header. The list
#: is the standard's synopsis for each one, with the conditional
#: families left out: `<fenv.h>`'s Annex F pragmas, the `_s` functions
#: of Annex K, and the `*_SNAN` and IEC 60559 additions this
#: implementation does not claim -- `<float.h>` says why.
C23_LIBRARY: dict[str, str] = {
    "complex.h": """
        cacos cacosh casin casinh catan catanh ccos ccosh csin csinh ctan
        ctanh cexp clog cabs cpow csqrt carg cimag conj cproj creal cacosf
        cacoshf casinf casinhf catanf catanhf ccosf ccoshf csinf csinhf ctanf
        ctanhf cexpf clogf cabsf cpowf csqrtf cargf cimagf conjf cprojf crealf
        cacosl cacoshl casinl casinhl catanl catanhl ccosl ccoshl csinl csinhl
        ctanl ctanhl cexpl clogl cabsl cpowl csqrtl cargl cimagl conjl cprojl
        creall
    """,
    "ctype.h": """
        isalnum isalpha isblank iscntrl isdigit isgraph islower isprint
        ispunct isspace isupper isxdigit tolower toupper
    """,
    "fenv.h": """
        feclearexcept fegetexceptflag feraiseexcept fesetexceptflag
        fetestexcept fegetround fesetround fegetenv feholdexcept fesetenv
        feupdateenv
    """,
    "inttypes.h": """
        imaxabs imaxdiv strtoimax strtoumax wcstoimax wcstoumax
    """,
    "locale.h": """
        setlocale localeconv
    """,
    "math.h": """
        acos asin atan atan2 cos sin tan acosh asinh atanh cosh sinh tanh exp
        exp2 expm1 frexp ilogb ldexp log log10 log1p log2 logb modf scalbn
        scalbln cbrt fabs hypot pow sqrt erf erfc lgamma tgamma ceil floor
        nearbyint rint lrint llrint round lround llround trunc fmod remainder
        remquo copysign nan nextafter nexttoward fdim fmax fmin fma acosf
        asinf atanf atan2f cosf sinf tanf acoshf asinhf atanhf coshf sinhf
        tanhf expf exp2f expm1f frexpf ilogbf ldexpf logf log10f log1pf log2f
        logbf modff scalbnf scalblnf cbrtf fabsf hypotf powf sqrtf erff erfcf
        lgammaf tgammaf ceilf floorf nearbyintf rintf lrintf llrintf roundf
        lroundf llroundf truncf fmodf remainderf remquof copysignf nanf
        nextafterf nexttowardf fdimf fmaxf fminf fmaf acosl asinl atanl atan2l
        cosl sinl tanl acoshl asinhl atanhl coshl sinhl tanhl expl exp2l
        expm1l frexpl ilogbl ldexpl logl log10l log1pl log2l logbl modfl
        scalbnl scalblnl cbrtl fabsl hypotl powl sqrtl erfl erfcl lgammal
        tgammal ceill floorl nearbyintl rintl lrintl llrintl roundl lroundl
        llroundl truncl fmodl remainderl remquol copysignl nanl nextafterl
        nexttowardl fdiml fmaxl fminl fmal
    """,
    "setjmp.h": """
        longjmp
    """,
    "signal.h": """
        signal raise
    """,
    "stdio.h": """
        remove rename tmpfile tmpnam fclose fflush fopen freopen setbuf
        setvbuf fprintf fscanf printf scanf snprintf sprintf sscanf vfprintf
        vfscanf vprintf vscanf vsnprintf vsprintf vsscanf fgetc fgets fputc
        fputs getc getchar puts putc putchar ungetc fread fwrite fgetpos fseek
        fsetpos ftell rewind clearerr feof ferror perror
    """,
    "stdlib.h": """
        atof atoi atol atoll strtod strtof strtold strtol strtoll strtoul
        strtoull strfromd strfromf strfroml rand srand aligned_alloc calloc
        free malloc realloc free_sized free_aligned_sized abort atexit
        at_quick_exit exit _Exit getenv quick_exit system bsearch qsort abs
        labs llabs div ldiv lldiv mblen mbtowc wctomb mbstowcs wcstombs
        memalignment
    """,
    "string.h": """
        memcpy memmove strcpy strncpy strdup strndup strcat strncat memcmp
        strcmp strcoll strncmp strxfrm memchr strchr strcspn strpbrk strrchr
        strspn strstr strtok memset memset_explicit strerror strlen memccpy
    """,
    "threads.h": """
        call_once cnd_broadcast cnd_destroy cnd_init cnd_signal cnd_timedwait
        cnd_wait mtx_destroy mtx_init mtx_lock mtx_timedlock mtx_trylock
        mtx_unlock thrd_create thrd_current thrd_detach thrd_equal thrd_exit
        thrd_join thrd_sleep thrd_yield tss_create tss_delete tss_get tss_set
    """,
    "time.h": """
        clock difftime mktime time timespec_get timespec_getres asctime ctime
        gmtime localtime strftime
    """,
    "uchar.h": """
        mbrtoc8 c8rtomb mbrtoc16 c16rtomb mbrtoc32 c32rtomb
    """,
    "wchar.h": """
        fwprintf fwscanf swprintf swscanf vfwprintf vfwscanf vswprintf
        vswscanf vwprintf vwscanf wprintf wscanf fgetwc fgetws fputwc fputws
        fwide getwc getwchar putwc putwchar ungetwc wcstod wcstof wcstold
        wcstol wcstoll wcstoul wcstoull wcscpy wcsncpy wmemcpy wmemmove wcscat
        wcsncat wcscmp wcscoll wcsncmp wcsxfrm wmemcmp wcschr wcscspn wcspbrk
        wcsrchr wcsspn wcsstr wcstok wmemchr wcslen wmemset wcsftime btowc
        wctob mbsinit mbrlen mbrtowc wcrtomb mbsrtowcs wcsrtombs
    """,
    "wctype.h": """
        iswalnum iswalpha iswblank iswcntrl iswdigit iswgraph iswlower
        iswprint iswpunct iswspace iswupper iswxdigit iswctype wctype towlower
        towupper towctrans wctrans
    """,
}


class TestTheStandardHeaders:
    @harness.cases("name", C23_HEADERS)
    def test_each_one_is_there_and_stands_alone(self, name):
        """ALL THIRTY-ONE COMPILE NOW. `REFUSING` is empty and is kept
        because the shape of the answer -- a header that is present and
        explains itself -- is what this suite is checking for."""
        module, sink = compile_c(f"#include <{name}>\nint main(void){{return 0;}}")
        codes_ = [d.code for d in sink.diagnostics]
        if name in REFUSING:
            assert codes_ == ["E1112"], codes_
            assert len(sink.diagnostics[0].message) > 40, "the reason is missing"
            return
        assert module is not None, codes_
        assert not sink.failed, codes_

    def test_every_macro_c23_requires_is_defined(self):
        """Asked with `#ifndef`, which is the only thing that can tell a
        macro from a name that merely exists: `INFINITY` has to be one, and
        a `static const float` of the same value would answer every test but
        this one.

        ONE TRANSLATION UNIT AGAIN, and it includes every header at once --
        which the test below this one relies on anyway.
        """
        body = [f"#include <{h}>" for h in sorted(C23_MACROS)]
        for header, names in sorted(C23_MACROS.items()):
            for name in names.split():
                if name.startswith("/*") or name.endswith("*/"):
                    continue
                body.append(f"#ifndef {name}")
                body.append(f'#error "{header} does not define {name}"')
                body.append("#endif")
        body.append("int main(void){ return 0; }")
        module, sink = compile_c("\n".join(body) + "\n")
        assert module is not None, [d.message for d in sink.diagnostics][:6]
        assert not sink.failed, [d.message for d in sink.diagnostics][:6]

    def test_every_function_c23_requires_is_declared(self, tmp_path):
        """THE NAMES, ALL FOUR HUNDRED AND FIFTY, asked for by taking each
        one's ADDRESS -- which a macro cannot answer and a missing
        declaration cannot either. That is the difference between this and
        the differential programs: those check what a handful of them DO,
        and a library is not conforming because its `printf` is right.

        ONE TRANSLATION UNIT AND NOT FOUR HUNDRED AND FIFTY, because the
        suite is run on every change and each compile is a preprocessor, a
        parser and a lowering over the whole of `<math.h>`.
        """
        body = []
        for header in sorted(C23_LIBRARY):
            body.append(f"#include <{header}>")
        body.append("void *__names[] = {")
        for _, names in sorted(C23_LIBRARY.items()):
            for name in names.split():
                body.append(f"    (void *)&{name},")
        body.append("};")
        body.append("int main(void){ return __names[0] != 0 ? 0 : 1; }")
        module, sink = compile_c("\n".join(body) + "\n")
        assert module is not None, [d.message for d in sink.diagnostics][:6]
        assert not sink.failed, [d.message for d in sink.diagnostics][:6]

    def test_all_of_them_at_once(self):
        """Including every header a program could include, together. A macro
        one of them defines can break the next; the only way to know is to
        put them all in one translation unit."""
        includes = "".join(f"#include <{h}>\n"
                           for h in C23_HEADERS if h not in REFUSING)
        module, sink = compile_c(includes + "int main(void){ return 0; }")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]

    def test_float_h_has_c23s_own_macros(self):
        """`*_IS_IEC_60559` and `*_NORM_MAX` are C23's, which the
        differential suite cannot compare because the host build is
        `-std=c11` and gcc hides them there. `*_SNAN` is absent on
        purpose -- a signaling NaN needs an exception flag, and the IR has
        no instruction that reads one -- so this checks that too."""
        module, sink = compile_c(
            "#include <float.h>\n"
            "_Static_assert(FLT_IS_IEC_60559 == 1, \"\");\n"
            "_Static_assert(DBL_IS_IEC_60559 == 1, \"\");\n"
            "_Static_assert(LDBL_IS_IEC_60559 == 1, \"\");\n"
            "_Static_assert(FLT_NORM_MAX == FLT_MAX, \"\");\n"
            "_Static_assert(DBL_NORM_MAX == DBL_MAX, \"\");\n"
            "_Static_assert(LDBL_NORM_MAX == LDBL_MAX, \"\");\n"
            "_Static_assert(DECIMAL_DIG == LDBL_DECIMAL_DIG, \"\");\n"
            "#if defined(FLT_SNAN) || defined(__STDC_IEC_559__)\n"
            "#error \"claiming what there is no status flag to support\"\n"
            "#endif\n"
            "int main(void){ return 0; }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]


class TestTheDivergences:
    """`frontends/c/__init__.py` names the places this frontend differs from
    a hosted implementation, and calls that list complete. A list nothing
    checks becomes a list of the ones somebody remembered.

    THE NUMBER IS IN THE DOCSTRING AND NOT IN THIS NAME on purpose: it was
    `TestTheFourDivergences` until one was added, and a class that has to be
    renamed to record a fact is a class that will not be."""

    def test_long_double_is_eighty_bit_extended(self):
        """x86-64's format, in software: sixteen bytes, ten in use, and a
        64-bit significand -- so a program's `sizeof` and `LDBL_*` agree
        with a hosted compiler's."""
        assert C.LDOUBLE.size == 16 and C.LDOUBLE.align == 16
        assert C.LDOUBLE.in_memory        # no IR type; it is carried by address
        module, sink = compile_c(
            "#include <float.h>\n"
            "_Static_assert(sizeof(long double) == 16, \"\");\n"
            "_Static_assert(LDBL_MANT_DIG == 64, \"\");\n"
            "_Static_assert(LDBL_MAX_EXP == 16384, \"\");\n"
            "long double f(long double a, long double b){ return a * b; }\n")
        assert not sink.failed, [d.message for d in sink.diagnostics]
        names = {fn.name for fn in module.functions}
        assert "__c_ldmul" in names, sorted(names)

    def test_a_long_double_constant_keeps_all_its_bits(self):
        """A `Fraction`, not a Python float: folding through a double would
        lose eleven of the sixty-four before the program ever ran."""
        module, _ = compile_c(
            "long double pi = 3.14159265358979323846L;\n"
            "int main(void){ return 0; }\n")
        got = [g for g in module.globals if g.name.endswith("pi")]
        assert got and got[0].data[:10].hex() == "35c26821a2da0fc90040"

    def test_complex_is_two_of_its_element(self):
        """The layout C requires, which is what `creal` and `CMPLX` read."""
        module, sink = compile_c(
            "#include <complex.h>\n"
            "_Static_assert(sizeof(double _Complex) == 16, \"\");\n"
            "_Static_assert(sizeof(float _Complex) == 8, \"\");\n"
            "_Static_assert(_Alignof(double _Complex) == 8, \"\");\n"
            "double _Complex z = 3.0 + 4.0 * I;\n"
            "double re(void){ return __real__ z; }\n")
        assert not sink.failed, [d.message for d in sink.diagnostics]
        got = [g for g in module.globals if g.name.endswith("z")]
        assert got and got[0].size == 16

    def test_imaginary_types_are_refused_as_annex_g_allows(self):
        _, sink = compile_c("_Imaginary double z;")
        assert [d.code for d in sink.diagnostics] == ["E1214"]
        assert "Annex G" in sink.diagnostics[0].notes[0]

    def test_threads_are_a_capability_of_the_target(self):
        """`<threads.h>` is `objects/hostsvc.py`'s `thread` group, so a
        program that starts one names it and a program that does not is
        untouched."""
        module, sink = compile_c(
            "#include <threads.h>\n"
            "static int go(void *p){ (void)p; return 1; }\n"
            "int main(void){ thrd_t t; thrd_create(&t, go, 0);\n"
            "                return thrd_join(t, 0); }")
        assert not sink.failed, [d.message for d in sink.diagnostics]
        assert "host_thread_start" in {f.name for f in module.functions}

    def test_thread_local_is_one_copy_per_thread(self):
        """Not `static` with a different spelling: every use is a lookup,
        and the key is made before `main` runs."""
        module, sink = compile_c(
            "_Thread_local int mine = 7;\n"
            "int get(void){ return mine; }\n"
            "int main(void){ return get(); }")
        assert not sink.failed, [d.message for d in sink.diagnostics]
        names = {f.name for f in module.functions}
        assert "__c_tls_get" in names and "host_tss_new" in names
        assert [g for g in module.globals if g.name.endswith("mine.key")]

    def test_nodiscard_and_deprecated_are_the_two_that_warn(self):
        """`attributes.py` divides the standard attributes into the ones
        with an effect here and the ones without. These are the two, and
        both are diagnostics at the point of USE rather than of
        declaration -- which is why they live on the symbol."""
        module, sink = compile_c(
            "[[nodiscard]] static int f(void){ return 1; }\n"
            "[[deprecated]] static int g(void){ return 2; }\n"
            "int main(void){ f(); (void)f(); return g(); }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]
        codes = [d.code for d in sink.diagnostics]
        assert codes.count("W1221") == 1, codes   # the dropped result
        assert codes.count("W1219") == 1, codes   # the deprecated call
        # `(void)f()` IS THE WAY TO SAY "I MEANT IT" and must not warn.

    def test_an_unknown_attribute_is_ignored_and_named(self):
        """C says an implementation ignores an attribute it does not know.
        An UNPREFIXED one is in the standard's namespace, so it is worth a
        word; a prefixed one belongs to somebody else and is not."""
        module, sink = compile_c(
            "[[no_such_thing]] static int a(void){ return 1; }\n"
            "[[gnu::always_inline]] static int b(void){ return 2; }\n"
            "int main(void){ return a() + b() - 3; }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]
        codes = [d.code for d in sink.diagnostics]
        assert codes.count("W1218") == 1, codes

    def test_auto_without_a_type_is_not_a_storage_class(self, tmp_path):
        """The four ways C23's `auto` is refused, all of which compiled
        before as `int`: a declarator built on it, no initialiser, a `void`
        initialiser, and an array. The WORKING cases are compared against a
        real compiler in the differential suite -- `auto` inference is one
        of the few C23 features gcc 13 has."""
        for source, code in (
                ("int main(void){ auto *p = (int *)0; return 0; }", "E1286"),
                ("int main(void){ auto a[2] = {1, 2}; return 0; }", "E1286"),
                ("int main(void){ auto x; return 0; }", "E1287"),
                ("void f(void);\nint main(void){ auto x = f(); return 0; }",
                 "E1288")):
            _, sink = compile_c(source)
            assert sink.failed, source
            assert code in [d.code for d in sink.diagnostics], source

        # AND `auto int x` IS STILL THE OLD KEYWORD, which has meant nothing
        # since C89 gave every block-scope object automatic storage anyway.
        module, sink = compile_c("int main(void){ auto int x = 1; return x - 1; }")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]

    def test_embed_is_the_file_s_bytes(self, tmp_path):
        """`#embed` IS A DIRECTIVE AND NOT A FUNCTION, which is why it needs
        a file on disk to test: the bytes are read at translation time and
        come out as a comma-separated list of integer constants.

        gcc 13 HAS NO `#embed` -- it landed in 15 -- so there is no oracle
        for it and this checks the bytes in the module directly."""
        (tmp_path / "data.bin").write_bytes(b"ABCDE")
        (tmp_path / "empty.bin").write_bytes(b"")
        src = tmp_path / "t.c"
        src.write_text(
            'static const unsigned char all[] = {\n#embed "data.bin"\n};\n'
            'static const unsigned char some[] = {\n'
            '#embed "data.bin" limit(3)\n};\n'
            'static const unsigned char both[] = {\n'
            '#embed "data.bin" prefix(1, ) suffix(, 2)\n};\n'
            'static const unsigned char none[] = {\n'
            '#embed "empty.bin" if_empty(9)\n};\n'
            '#if __has_embed("data.bin") != __STDC_EMBED_FOUND__\n'
            '#error "should have been found"\n#endif\n'
            '#if __has_embed("empty.bin") != __STDC_EMBED_EMPTY__\n'
            '#error "should have been found and empty"\n#endif\n'
            '#if __has_embed("nope.bin") != __STDC_EMBED_NOT_FOUND__\n'
            '#error "should not have been found"\n#endif\n'
            "int main(void){ return all[0] + some[0] + both[0] + none[0]; }\n",
            encoding="utf-8")
        sink = DiagnosticSink()
        module = c_frontend.CFrontend().compile(SourceFile.read(src), sink)
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]
        data = {g.name.split(".")[-1]: bytes(g.data or b"")
                for g in module.globals}
        assert data["all"] == b"ABCDE", data
        assert data["some"] == b"ABC", data
        # `prefix` AND `suffix` ARE DROPPED WHEN THE RESOURCE IS EMPTY, which
        # is the difference between them and writing the values by hand.
        assert data["both"] == b"\x01ABCDE\x02", data
        assert data["none"] == b"\x09", data

    def test_pragma_operator_is_a_pragma(self, tmp_path):
        """`_Pragma("once")` is `#pragma once` written where a token goes,
        and it is processed AFTER macro replacement -- which is why a header
        may write `_Pragma(STRINGIFY(x))` and why this lives in the
        expander rather than in the directive loop."""
        header = tmp_path / "guarded.h"
        header.write_text('_Pragma("once")\nstatic int guarded_value = 7;\n',
                          encoding="utf-8")
        src = tmp_path / "t.c"
        src.write_text('#include "guarded.h"\n#include "guarded.h"\n'
                       '#define P(x) _Pragma(#x)\nP(pack(1))\n'
                       "int main(void){ _Pragma(\"STDC FP_CONTRACT ON\")\n"
                       "                return guarded_value - 7; }\n",
                       encoding="utf-8")
        sink = DiagnosticSink()
        module = c_frontend.CFrontend().compile(SourceFile.read(src), sink)
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]

        # AND IT IS AN OPERATOR, so a bare one is not an identifier the
        # parser should be left to puzzle over two phases later.
        _, sink = compile_c("int main(void){ return _Pragma; }")
        assert "E1148" in [d.code for d in sink.diagnostics]

    def test_a_bitint_wider_than_the_maximum_is_refused_by_name(self):
        """`BITINT_MAXWIDTH` is 64 here, which C23 permits -- it requires
        only `ULLONG_WIDTH`. The refusal names the number rather than
        failing somewhere in lowering, and `<limits.h>` reports the same
        one so a program can ask before it asks for the type."""
        module, sink = compile_c("_BitInt(65) too_wide;\n"
                                 "int main(void){ return 0; }\n")
        assert sink.failed
        codes = [d.code for d in sink.diagnostics]
        assert "E1283" in codes, codes
        assert "64" in sink.diagnostics[0].message

        # A SIGNED ONE NEEDS TWO BITS, an unsigned one needs one: the sign
        # bit is not a value bit, and a type with no value bits is not one.
        module, sink = compile_c("_BitInt(1) narrow;\nint main(void){return 0;}")
        assert sink.failed
        assert "E1284" in [d.code for d in sink.diagnostics]
        module, sink = compile_c("unsigned _BitInt(1) b;\n"
                                 "int main(void){ return (int)b; }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]

        # AND ONLY `signed` AND `unsigned` GO WITH IT.
        module, sink = compile_c("long _BitInt(8) bad;\n"
                                 "int main(void){ return 0; }\n")
        assert sink.failed
        assert "E1281" in [d.code for d in sink.diagnostics]

    def test_a_parameter_in_a_definition_need_not_be_named(self):
        """C23's, and the point of it is that the argument still ARRIVES.
        Dropping an unnamed parameter instead of giving it a nameless place
        would shift every argument after it by one, which is a wrong answer
        rather than a diagnostic -- so the test is a call, not a compile."""
        module, sink = compile_c(
            "static int f(int a, int, const char *, int b){ return a + b; }\n"
            "int main(void){ return f(1, 99, \"x\", 2); }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]

        # AN INCOMPLETE TYPE IS STILL AN INCOMPLETE TYPE, named or not, and
        # the diagnostic counts to it rather than quoting a name it has not
        # got.
        _, sink = compile_c("struct opaque;\n"
                            "int f(int, struct opaque){ return 0; }\n"
                            "int main(void){ return 0; }\n")
        assert sink.failed
        assert "E1282" in [d.code for d in sink.diagnostics]
        assert "parameter 2" in sink.diagnostics[0].message

    def test_a_compound_literal_may_say_static(self):
        """C23 again, and the reason is the initialiser below it: a literal
        inside a function used to have automatic storage and no constant
        address, so `static int *p = (int[]){1};` had nowhere to point."""
        module, sink = compile_c(
            "int *f(void){ static int *p = (static int[]){1, 2, 3};\n"
            "              return p; }\n"
            "int main(void){ return f()[0] - 1; }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]

        # WITHOUT IT THE ADDRESS IS NOT A CONSTANT, which is what the
        # specifier changes and what the host compiler says too.
        _, sink = compile_c("int *f(void){ static int *p = (int[]){1};\n"
                            "              return p; }\n"
                            "int main(void){ return 0; }\n")
        assert sink.failed

    @harness.cases("source,code", [
        # `thread_local` needs company: a compound literal cannot be
        # `extern`, so `static` is the only company C leaves it.
        ("int *f(void){ return (thread_local int[]){1}; }", "E1606"),
        # A CAST CANNOT HAVE A STORAGE CLASS, so this is a literal missing
        # its braces rather than a cast with a stray word in it.
        ("int f(int x){ return (static int)x; }", "E1605"),
        ("int *f(void){ return (static static int[]){1}; }", "E1604"),
        # `constexpr` says the value is known now, and `n` is not.
        ("int f(int n){ return (constexpr int){n}; }", "E1607"),
        # `register` buys exactly one thing and this is it.
        ("int *f(void){ return &(register int){1}; }", "E1412"),
        ("int f(void){ register int x = 1; return (int)(long)&x; }", "E1412"),
    ])
    def test_the_storage_classes_a_literal_may_not_have(self, source, code):
        _, sink = compile_c(source + "\nint main(void){ return 0; }\n")
        got = [d.code for d in sink.diagnostics]
        assert code in got, got

    def test_a_bitint_is_not_promoted(self):
        """The whole reason the type is useful: `a + b` on two `_BitInt(4)`s
        is arithmetic in four bits and wraps there. A promotion to `int`
        would have made it arithmetic in 32, and the wrapping would have
        happened only on the way back into the object -- which is a
        different answer for `*` and for `<<`."""
        assert C.promote(C.bitint(4, True)) == C.bitint(4, True)
        assert C.usual_arithmetic(C.bitint(4, True),
                                  C.bitint(4, True)) == C.bitint(4, True)
        # A standard type as wide or wider outranks it; a narrower one does
        # not, which is C23's rank rule stated against the width.
        assert C.usual_arithmetic(C.bitint(4, True), C.INT) == C.INT
        assert C.usual_arithmetic(C.bitint(40, True), C.INT) \
            == C.bitint(40, True)
        assert C.usual_arithmetic(C.bitint(32, True), C.UINT) == C.UINT
        assert C.usual_arithmetic(C.bitint(33, True), C.UINT) \
            == C.bitint(33, True)
        # Two of the same width differ only in signedness, and the unsigned
        # one wins -- the same rule the standard types have.
        assert C.usual_arithmetic(C.bitint(4, True), C.bitint(4, False)) \
            == C.bitint(4, False)
        # And the width is part of the type, which the kind alone does not
        # say: two of different widths are no more compatible than `short`
        # and `int`.
        assert not C.compatible(C.bitint(13, True), C.bitint(14, True))
        assert C.compatible(C.bitint(13, True), C.bitint(13, True))

    def test_constexpr_needs_a_constant_and_then_is_one(self):
        """The two halves of C23's `constexpr`: the initialiser must fold,
        and afterwards the NAME folds. Both are refusals rather than silent
        behaviour, which is why they are checked here and the working case
        is compared against a real compiler in the differential suite."""
        module, sink = compile_c(
            "int f(void);\n"
            "int main(void){ constexpr int n = f(); return n; }\n")
        assert sink.failed
        assert [d.code for d in sink.diagnostics if d.code.startswith("E")] \
            == ["E1280"], [d.code for d in sink.diagnostics]

        module, sink = compile_c("int main(void){ constexpr int n; return n; }")
        assert sink.failed
        assert "E1279" in [d.code for d in sink.diagnostics]

        # AND THE NAME IS A CONSTANT: an array bound written with one is an
        # ordinary array rather than a variable-length one, which is the
        # difference a program can see.
        module, sink = compile_c(
            "constexpr int n = 4;\n"
            "_Static_assert(n == 4, \"\");\n"
            "int main(void){ int a[n]; a[3] = 1; return a[3] - 1; }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]
        assert not any(f.name.endswith("vla_alloc") for f in module.functions)

    def test_a_hex_escape_is_a_byte_and_a_character_is_a_character(self):
        """The one distinction `encode` cannot make for itself, because by
        the time both are numbers they look identical: `"\\xe9"` is the one
        byte it was written as and `"\\u00e9"` is the two UTF-8 spells that
        character with. `decode_escapes` records which is which."""
        from uasm.frontends.c.literals import decode_escapes, encode
        flags: list[bool] = []
        values = decode_escapes("\\xe9", max_value=0xFF, literal=flags)
        assert values == [0xE9] and flags == [True]
        assert encode(values, "", flags) == b"\xe9"

        flags = []
        values = decode_escapes("\\u00e9", max_value=0xFF, literal=flags)
        assert values == [0xE9] and flags == [False]
        assert encode(values, "", flags) == b"\xc3\xa9"

        # AND A UCN IS NOT CHECKED AGAINST THE ELEMENT'S WIDTH, because it
        # names a character rather than a value the element has to hold:
        # `"\\u0100"` in a narrow string is a perfectly good two-byte one.
        flags = []
        assert decode_escapes("\\u0100", max_value=0xFF, literal=flags) == [0x100]
        # A surrogate is not a character, and C says so.
        from uasm.frontends.c.literals import LiteralError
        with harness.raises(LiteralError, match="not a character"):
            decode_escapes("\\ud800", max_value=0x10FFFF)

    def test_a_streams_orientation_is_recorded_and_not_enforced(self):
        """C says a stream takes an orientation from its first operation
        and leaves the other kind undefined afterwards. There is one buffer
        under both faces here, so both keep working -- and `fwide` still
        has to answer truthfully, which is what a program that asks wants
        to know."""
        module, sink = compile_c(
            "#include <stdio.h>\n#include <wchar.h>\n"
            "int main(void){\n"
            "  if (fwide(stdout, 0) != 0) return 1;\n"
            "  printf(\"x\");\n"
            "  if (fwide(stdout, 1) != -1) return 2;\n"
            "  fwprintf(stdout, L\"y\");\n"
            "  return 0; }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]

    def test_the_multibyte_encoding_is_utf_8(self):
        """C leaves the execution character set to the implementation, and
        this one chose the source's. glibc's `"C"` locale chose one byte per
        character, which is why a program can tell the two apart."""
        module, sink = compile_c(
            "#include <stdlib.h>\n"
            "_Static_assert(MB_CUR_MAX == 4, \"\");\n"
            "#include <limits.h>\n"
            "_Static_assert(MB_LEN_MAX >= MB_CUR_MAX, \"\");\n"
            "int main(void){ return (int)mbstowcs(0, \"\\xc3\\xa9\", 0); }\n")
        assert module is not None, [d.message for d in sink.diagnostics]
        assert not sink.failed, [d.message for d in sink.diagnostics]

    def test_setjmp_compiles_into_the_function_that_calls_it(self):
        """There is no `setjmp` function to call: `longjmp.py` turns the
        call into a branch and every call site into a check."""
        module, sink = compile_c(
            "#include <setjmp.h>\n"
            "static jmp_buf e;\n"
            "static void go(void){ longjmp(e, 1); }\n"
            "int main(void){ if (setjmp(e) == 0) go(); return 0; }")
        assert not sink.failed, [d.message for d in sink.diagnostics]
        names = {f.name for f in module.functions}
        assert "__c_setjmp" not in names and "__c_longjmp" not in names
        assert {"__c_jmp_active", "__c_jmp_target"} <= {
            g.name for g in module.globals}

    def test_the_address_of_setjmp_is_refused(self):
        _, sink = compile_c(
            "#include <setjmp.h>\n"
            "int (*f)(void *) = __c_setjmp;\n"
            "int main(void){ return f == 0; }")
        assert "E1603" in [d.code for d in sink.diagnostics]

    def test_a_program_without_setjmp_is_not_rewritten(self):
        """The check after every call is what `setjmp` costs, and a program
        that does not use it must not pay."""
        module, _ = compile_c(
            "#include <stdio.h>\nint main(void){ puts(\"hi\"); return 0; }")
        assert not [g for g in module.globals if g.name.startswith("__c_jmp")]

    def test_main_with_parameters_gets_the_command_line(self):
        """`argc` and `argv` are built from the `env` host service by the
        support unit, and only when `main` asks for them."""
        module, sink = compile_c(
            "int main(int argc, char **argv){ return argc; }")
        assert not sink.failed, [d.message for d in sink.diagnostics]
        names = {f.name for f in module.functions}
        assert {"__c_args_count", "__c_args_build"} <= names
        assert "host_arg_count" in names

    def test_main_without_parameters_needs_no_host_services(self):
        """The floor is enough for a program that does not ask, which is
        what keeps one runnable on a backend with no environment."""
        module, _ = compile_c("int main(void){ return 0; }")
        names = {f.name for f in module.functions}
        assert not [n for n in names if n.startswith("host_")]

    def test_a_third_parameter_is_null_and_says_so(self):
        _, sink = compile_c(
            "int main(int c, char **v, char **e){ return e == 0; }")
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
        into the unit's own initialiser, which the entry point calls first.
        The name carries the unit's prefix because a build may have several
        and the entry point calls all of them."""
        module, _ = compile_c(
            "int v = 7; int *p = &v; int main(void){ return *p; }")
        assert module.function("c0.init") is not None
        entry = module.function("main")
        calls = [i.sym for _, i in entry.instructions() if i.sym]
        assert calls[0] == "c0.init"

    def test_a_struct_is_returned_through_a_hidden_parameter(self):
        module, _ = compile_c(
            "struct p { int a, b; };\n"
            "struct p make(int n){ struct p r = {n, n+1}; return r; }\n"
            "int main(void){ return make(1).b; }")
        make = module.function("make")
        assert str(make.ret) == "void"
        assert str(make.registers[make.params[0]]) == "ptr"
