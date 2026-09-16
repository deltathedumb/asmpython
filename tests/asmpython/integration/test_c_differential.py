"""C programs, three ways, all of which must agree.

    1. the host's `cc`, compiled and run                 -- the oracle
    2. this frontend's IR, in the reference interpreter
    3. this frontend's IR through the C backend, compiled and run

(1) IS THE ORACLE AND THAT IS THE POINT OF THE WHOLE FILE. A C frontend can be
self-consistently wrong: it can agree with itself about what `-17 / 5` is, what
a struct's second member's offset is, and what `%g` prints, and be wrong about
all three. The only check that catches that is another C compiler, and there is
one on almost every machine.

(3) IS WHAT CATCHES A BACKEND THAT DISAGREES WITH THE INTERPRETER. It is also
what makes the lowering conventions real rather than theoretical: a struct
returned by value, a variadic call's argument area and a variable-length
array's arena all have to survive the trip through actual machine code, not
only through a Python loop that implements the same opcodes the same way.

THE PROGRAMS ARE WRITTEN TWICE-COMPILABLE. Each one gets a prelude that
declares `plat_write` -- the floor this frontend's library sits on -- and the
host build defines it over `write(2)`. Everything above that line is ordinary C
and is compiled from the same text by both.

Guarded on `cc`: a machine without one runs everything else, because a red
suite people are told to ignore is worse than a smaller green one.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from io import StringIO
from pathlib import Path

from tests import harness

from asmpython.diagnostics import DiagnosticSink, SourceFile
from asmpython.frontends import c as c_frontend
from asmpython.ir import verify
from asmpython.ir.interpreter import Interpreter

HAS_CC = shutil.which("gcc") or shutil.which("cc")

#: What the host build puts above the program so that `plat_write` exists.
#: The three floor functions are all this library needs; only the one that
#: writes is reachable from a program that prints.
HOST_PRELUDE = """\
#include <unistd.h>
#include <stdlib.h>
static long plat_write(long fd, const void *b, long n)
{ return (long)write((int)fd, b, (size_t)n); }
static void plat_exit(long c) { exit((int)c); }
static void *plat_heap(long n) { return malloc((size_t)n); }
"""

#: And what this frontend puts there: a declaration, because the backend or
#: the interpreter supplies the definition.
OURS_PRELUDE = """\
extern long plat_write(long fd, const void *b, long n);
extern void plat_exit(long c);
extern void *plat_heap(long n);
"""


def _host_run(source: str, tmp_path: Path) -> tuple[int, str]:
    src = tmp_path / "host.c"
    exe = tmp_path / "host.exe"
    src.write_text(HOST_PRELUDE + source, encoding="utf-8")
    built = subprocess.run(
        [HAS_CC, "-std=c11", "-w", "-o", str(exe), str(src), "-lm"],
        capture_output=True, text=True)
    assert built.returncode == 0, f"the host compiler refused it:\n{built.stderr}"
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    return ran.returncode, ran.stdout


def _compile(source: str):
    sink = DiagnosticSink()
    c_frontend.use()
    module = c_frontend.CFrontend().compile(
        SourceFile(OURS_PRELUDE + source, "prog.c"), sink)
    assert module is not None, [d.message for d in sink.diagnostics]
    assert not sink.failed, [d.message for d in sink.diagnostics]
    verify(module)
    return module


def _interpret(module) -> tuple[int, str]:
    out = StringIO()
    interpreter = Interpreter(module, out=out)
    value = interpreter.run("main")
    return int(value) & 0xFF, out.getvalue()


def _through_c_backend(module, tmp_path: Path) -> tuple[int, str]:
    from asmpython.backend import get, load_builtin
    from asmpython.target import get as get_target
    load_builtin()
    emitted = tmp_path / "out.c"
    emitted.write_bytes(get("c").emit(module, get_target("c"))["out.c"])
    exe = tmp_path / "out.exe"
    # -lm -ldl: the prelude the C backend writes calls libm and libdl
    # unconditionally on every ELF host -- see toolchains.py.
    libs = [] if sys.platform == "win32" else ["-lm", "-ldl"]
    built = subprocess.run([HAS_CC, "-w", "-o", str(exe), str(emitted), *libs],
                           capture_output=True, text=True)
    assert built.returncode == 0, f"the emitted C did not compile:\n{built.stderr}"
    ran = subprocess.run([str(exe)], capture_output=True, text=True)
    return ran.returncode, ran.stdout


def _normalise(result: tuple[int, str]) -> tuple[int, str]:
    """The SIGN OF A NaN is unspecified and the platforms disagree: x86 gives
    `0.0/0.0` a negative one and AArch64 a positive one, so neither spelling
    is the right answer and comparing them proves nothing."""
    status, text = result
    return status, text.replace("-nan", "nan").replace("-NAN", "NAN")


PROGRAMS: dict[str, str] = {
    "arithmetic": r"""
        #include <stdio.h>
        int main(void) {
            int a = 17, b = 5;
            printf("%d %d %d %d %d\n", a+b, a-b, a*b, a/b, a%b);
            printf("%d %d %d %d\n", -a/b, a/-b, -a%b, a%-b);
            printf("%d %d %d\n", 1 << 20, -8 >> 2, ~0);
            long big = 9223372036854775807L;
            printf("%ld %ld\n", big, big + 1);
            return 0;
        }
    """,
    "unsigned_and_promotion": r"""
        #include <stdio.h>
        int main(void) {
            unsigned u = 0xFFFFFFFFu;
            int i = -1;
            printf("%lu %u %d\n", (unsigned long)u, u + 1, (int)u);
            printf("%d %d %d\n", i < (int)u, (unsigned)i < u, i == -1);
            unsigned char c = 200;
            printf("%d %d\n", c + c, (unsigned char)(c + c));
            short s = -32768;
            printf("%d %d\n", s, (int)(short)(s - 1));
            return 0;
        }
    """,
    "floats": r"""
        #include <stdio.h>
        int main(void) {
            double d = 3.5; float f = 1.25f;
            printf("%f %f %e %g\n", d, (double)f, d * 1e10, d / 3.0);
            printf("%.0f %.0f %.0f %.0f\n", 0.5, 1.5, 2.5, -2.5);
            printf("%d %d %d\n", (int)3.9, (int)-3.9, (int)(d * 2));
            printf("%.17g %.17g\n", 0.1, 1.0 / 3.0);
            printf("%f\n", 1e300);
            return 0;
        }
    """,
    "pointers": r"""
        #include <stdio.h>
        int main(void) {
            int a[5] = {1,2,3,4,5};
            int *p = a;
            printf("%d %d %d %ld\n", *p, *(p+3), p[4], (long)(&a[3] - a));
            p += 2; printf("%d\n", *p);
            printf("%d\n", *--p);
            int **pp = &p;
            printf("%d\n", **pp);
            char s[] = "abc";
            printf("%c%c%c %d\n", s[0], s[1], s[2], (int)sizeof s);
            return 0;
        }
    """,
    "structs_by_value": r"""
        #include <stdio.h>
        struct P { int a, b; double d; };
        static int sum(struct P p) { p.a = 100; return p.a + p.b + (int)p.d; }
        static struct P make(int n) { struct P r; r.a = n; r.b = n+1; r.d = n*0.5; return r; }
        int main(void) {
            struct P p = {1, 2, 3.5};
            printf("%d %d\n", sum(p), p.a);
            struct P q = make(9);
            printf("%d %d %.1f\n", q.a, q.b, q.d);
            struct P r = q; r.a = 0;
            printf("%d %d\n", q.a, r.a);
            printf("%d\n", (int)sizeof(struct P));
            return 0;
        }
    """,
    "unions_and_bitfields": r"""
        #include <stdio.h>
        union U { int i; char b[4]; };
        struct B { unsigned a : 3; unsigned b : 9; int s : 4; char c; };
        int main(void) {
            union U u; u.i = 0x41424344;
            printf("%d %d %d\n", u.b[0], u.b[3], (int)sizeof u);
            struct B v; v.a = 5; v.b = 200; v.s = -3; v.c = 'z';
            printf("%u %u %d %c %d\n", v.a, v.b, v.s, v.c, (int)sizeof v);
            v.a += 2; v.s -= 1;
            printf("%u %d\n", v.a, v.s);
            return 0;
        }
    """,
    "control_flow": r"""
        #include <stdio.h>
        int main(void) {
            int total = 0;
            for (int i = 0; i < 10; i++) {
                if (i % 2) continue;
                if (i == 8) break;
                total += i;
            }
            printf("%d\n", total);
            int j = 0; while (j < 5) j++;
            do { j--; } while (j > 2);
            printf("%d\n", j);
            for (int i = 0; i < 6; i++)
                switch (i) {
                case 0: case 1: printf("a"); break;
                case 2: printf("b"); break;
                case 3: case 4: printf("c"); break;
                default: printf("d");
                }
            printf("\n");
            int k = 0;
        again:
            if (k < 3) { printf("%d", k); k++; goto again; }
            printf("\n");
            return 0;
        }
    """,
    "recursion_and_pointers_to_functions": r"""
        #include <stdio.h>
        static long fib(int n) { return n < 2 ? n : fib(n-1) + fib(n-2); }
        static int dbl(int x) { return x * 2; }
        static int neg(int x) { return -x; }
        static int apply(int (*f)(int), int x) { return f(x); }
        int main(void) {
            printf("%ld\n", fib(20));
            int (*table[2])(int) = { dbl, neg };
            printf("%d %d %d\n", apply(dbl, 21), table[1](5), (*table[0])(3));
            return 0;
        }
    """,
    "initialisers": r"""
        #include <stdio.h>
        struct S { int a, b, c; };
        static int grid[3][2] = {{1,2},{3,4},{5,6}};
        static struct S s = { .c = 3, .a = 1 };
        static char text[] = "hello";
        static int sparse[6] = { [4] = 9, [1] = 2 };
        static const char *names[] = { "x", "yy", "zzz" };
        int main(void) {
            printf("%d %d %d\n", grid[1][1], grid[2][0], (int)sizeof grid);
            printf("%d %d %d\n", s.a, s.b, s.c);
            printf("%s %d\n", text, (int)sizeof text);
            printf("%d %d %d\n", sparse[1], sparse[4], sparse[5]);
            printf("%s %s %s\n", names[0], names[1], names[2]);
            int local[4] = {7};
            printf("%d %d\n", local[0], local[3]);
            return 0;
        }
    """,
    "varargs": r"""
        #include <stdio.h>
        #include <stdarg.h>
        static long total(int n, ...) {
            va_list ap; long t = 0;
            va_start(ap, n);
            for (int i = 0; i < n; i++) t += va_arg(ap, int);
            va_end(ap);
            return t;
        }
        static void show(const char *fmt, ...) {
            va_list ap; va_start(ap, fmt); vprintf(fmt, ap); va_end(ap);
        }
        static double dtotal(int n, ...) {
            va_list ap, copy; double t = 0;
            va_start(ap, n);
            va_copy(copy, ap);
            for (int i = 0; i < n; i++) t += va_arg(copy, double);
            va_end(copy); va_end(ap);
            return t;
        }
        int main(void) {
            printf("%ld %ld\n", total(3,1,2,3), total(5,10,20,30,40,50));
            show("%s %d %.2f\n", "fmt", 42, 1.5);
            printf("%.2f\n", dtotal(3, 1.5, 2.5, 3.0));
            return 0;
        }
    """,
    "variable_length_arrays": r"""
        #include <stdio.h>
        static long work(int n) {
            int a[n];
            for (int i = 0; i < n; i++) a[i] = i * i;
            long t = 0;
            for (int i = 0; i < n; i++) t += a[i];
            return t;
        }
        int main(void) {
            for (int i = 1; i <= 5; i++) printf("%ld ", work(i));
            printf("\n");
            int n = 3;
            printf("%d\n", (int)sizeof(int[n]));
            return 0;
        }
    """,
    "string_library": r"""
        #include <stdio.h>
        #include <string.h>
        int main(void) {
            char a[32], b[32];
            strcpy(a, "hello "); strcat(a, "world");
            printf("%s %d\n", a, (int)strlen(a));
            memset(b, 'x', 5); b[5] = 0;
            printf("%s\n", b);
            printf("%d %d %d\n", strcmp("abc","abd") < 0, strcmp("abc","abc"),
                   strncmp("abcd","abce",3));
            printf("%s %s\n", strchr(a, 'w'), strstr(a, "lo w"));
            char c[8] = "abcdefg";
            memmove(c + 1, c, 6); c[7] = 0;
            printf("%s\n", c);
            printf("%d %d\n", (int)strspn("abcdef", "abc"),
                   (int)strcspn("abcdef", "de"));
            return 0;
        }
    """,
    "stdlib": r"""
        #include <stdio.h>
        #include <stdlib.h>
        static int cmp(const void *a, const void *b) {
            int x = *(const int *)a, y = *(const int *)b;
            return (x > y) - (x < y);
        }
        int main(void) {
            int *p = malloc(10 * sizeof *p);
            for (int i = 0; i < 10; i++) p[i] = (i * 7) % 10;
            qsort(p, 10, sizeof *p, cmp);
            for (int i = 0; i < 10; i++) printf("%d", p[i]);
            printf("\n");
            int key = 5;
            printf("%d\n", *(int *)bsearch(&key, p, 10, sizeof *p, cmp));
            p = realloc(p, 20 * sizeof *p);
            printf("%d\n", p[3]);
            free(p);
            char *z = calloc(8, 4);
            printf("%d\n", z[17]);
            free(z);
            printf("%d %ld %.1f\n", atoi("  -42xyz"), strtol("0x1f", 0, 0),
                   strtod("3.5e2", 0));
            printf("%d %d %d\n", abs(-9), div(17,5).quot, div(17,5).rem);
            return 0;
        }
    """,
    "printf_shapes": r"""
        #include <stdio.h>
        int main(void) {
            printf("[%5d][%-5d][%05d][%+d][% d]\n", 42, 42, 42, 42, 42);
            printf("[%x][%X][%#x][%o][%#o][%u]\n", 255, 255, 255, 8, 8, 4000000000u);
            printf("[%c][%s][%10s][%-10s][%.3s]\n", 'Z', "abc", "abc", "abc", "abcdef");
            printf("[%ld][%lu][%lx][%hd][%hhd]\n", 1234567890123L,
                   18446744073709551615UL, 0xdeadbeefUL, (short)70000, (char)300);
            printf("[%f][%.2f][%10.3f][%-10.3f|]\n", 3.5, 3.14159, 2.718281828, 2.718281828);
            printf("[%e][%.3e][%E]\n", 1234.5678, 0.000123456, 6.022e23);
            printf("[%g][%g][%g][%.10g]\n", 100000.0, 1000000.0, 0.0001, 3.14159265358979);
            printf("[%*d][%.*f][%%]\n", 6, 7, 3, 1.5);
            char b[16];
            int n = snprintf(b, sizeof b, "%s-%d", "xyz", 99);
            printf("[%s][%d]\n", b, n);
            n = snprintf(b, 4, "%s-%d", "abcdefgh", 99);
            printf("[%s][%d]\n", b, n);
            printf("[%s][%d]\n", (char *)0, 0);
            return 0;
        }
    """,
    "math_library": r"""
        #include <stdio.h>
        #include <math.h>
        int main(void) {
            printf("%.10f %.10f %.10f\n", sqrt(2.0), sqrt(0.25), cbrt(27.0));
            printf("%.10f %.10f %.10f\n", exp(1.0), log(10.0), log2(1024.0));
            printf("%.10f %.10f %.10f\n", pow(2.0,10.0), pow(2.0,0.5), pow(1.5,-3.0));
            printf("%.10f %.10f %.10f\n", sin(1.0), cos(1.0), tan(0.5));
            printf("%.10f %.10f\n", sin(100.0), cos(1000.0));
            printf("%.10f %.10f %.10f\n", atan(1.0), atan2(1.0,-1.0), asin(0.5));
            printf("%.10f %.10f %.10f\n", floor(-2.5), ceil(-2.5), round(-2.5));
            printf("%.10f %.10f %.10f\n", fmod(10.5,3.0), fmod(-10.5,3.0), hypot(3.0,4.0));
            printf("%d %d %d\n", isnan(NAN), isinf(INFINITY), isfinite(1.0));
            return 0;
        }
    """,
    "sizeof_and_alignment": r"""
        #include <stdio.h>
        struct Q { char c; double d; int i; };
        struct R { char a[3]; short b; };
        int main(void) {
            printf("%d %d %d %d %d %d\n", (int)sizeof(char), (int)sizeof(short),
                   (int)sizeof(int), (int)sizeof(long), (int)sizeof(double),
                   (int)sizeof(void *));
            printf("%d %d %d %d\n", (int)sizeof(struct Q), (int)_Alignof(struct Q),
                   (int)sizeof(struct R), (int)_Alignof(struct R));
            printf("%d %d %d\n", (int)__builtin_offsetof(struct Q, d),
                   (int)sizeof(int[7]), (int)sizeof("abcd"));
            return 0;
        }
    """,
    "generic_and_compound_literals": r"""
        #include <stdio.h>
        #define kind(x) _Generic((x), int: 1, double: 2, char *: 3, default: 0)
        struct V { int a, b; };
        static int sum(struct V v) { return v.a + v.b; }
        int main(void) {
            printf("%d %d %d %d\n", kind(1), kind(1.0), kind("s"), kind(1.0f));
            int *p = (int[]){10, 20, 30};
            printf("%d %d\n", p[2], sum((struct V){4, 5}));
            printf("%d\n", (int){7});
            return 0;
        }
    """,
    "assert_and_exit": r"""
        #include <stdio.h>
        #include <stdlib.h>
        #include <assert.h>
        static void bye(void) { printf("atexit\n"); }
        int main(void) {
            atexit(bye);
            assert(1 + 1 == 2);
            printf("%s\n", __func__);
            exit(7);
        }
    """,
    "linked_list": r"""
        #include <stdio.h>
        #include <stdlib.h>
        struct Node { int key; struct Node *next; };
        static struct Node *push(struct Node *head, int key) {
            struct Node *n = malloc(sizeof *n);
            n->key = key; n->next = head;
            return n;
        }
        int main(void) {
            struct Node *head = NULL;
            for (int i = 0; i < 8; i++) head = push(head, i * i);
            long total = 0;
            for (struct Node *p = head; p; p = p->next) { printf("%d ", p->key); total += p->key; }
            printf("\n%ld\n", total);
            while (head) { struct Node *n = head->next; free(head); head = n; }
            return 0;
        }
    """,
    "builtins": r"""
        #include <stdio.h>
        int main(void) {
            printf("%d %d %d\n", __builtin_popcount(0xFF), __builtin_clz(1u),
                   __builtin_ctz(8u));
            printf("%x %lx\n", __builtin_bswap32(0x11223344u),
                   (unsigned long)__builtin_bswap64(0x1122334455667788UL));
            printf("%d %d\n", __builtin_types_compatible_p(int, signed),
                   __builtin_constant_p(1 + 1));
            char a[8] = {0}, b[8] = "abcdefg";
            __builtin_memcpy(a, b, 8);
            printf("%s %d\n", a, __builtin_memcmp(a, b, 8));
            return 0;
        }
    """,

    # ── the second battery: shapes the first one does not reach ──────────
    "typedefs": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        typedef int (*Fn)(int);
        typedef int Arr[4];
        typedef struct { int a; char b; } Rec;
        typedef Rec *RecP;
        static int dbl(int x){return x+x;}
        int main(void){ Fn f = dbl; Arr a = {1,2,3,4}; Rec r = {5,'z'}; RecP p = &r;
          printf("%d %d %d %d %c %d\n", f(3), a[2], (int)sizeof(Arr), p->a, p->b, (int)sizeof(Rec)); return 0; }
    """,
    "qualifiers": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static const int c = 7;
        static volatile int v = 3;
        int main(void){ const int *p = &c; int *const q = (int*)&v;
          *q = 9; printf("%d %d %d\n", *p, *q, v);
          const char *s = "abc"; printf("%c\n", s[1]); return 0; }
    """,
    "array_params": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static int sum2(int m[3][4]){ int t=0; for(int i=0;i<3;i++) for(int j=0;j<4;j++) t+=m[i][j]; return t; }
        static int first(int (*p)[4]){ return p[1][2]; }
        static int n_elems(int a[static 3]){ return a[0]+a[2]; }
        int main(void){ int g[3][4]={{1,2,3,4},{5,6,7,8},{9,10,11,12}};
          printf("%d %d %d\n", sum2(g), first(g), n_elems(g[0])); return 0; }
    """,
    "static_locals": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static int counter(void){ static int n = 10; return ++n; }
        static int other(void){ static int n = 100; return ++n; }
        int main(void){ int a = counter(); int b = counter(); int c = other(); int d = counter();
          printf("%d %d %d %d\n", a, b, c, d); return 0; }
    """,
    "nested_designated": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct Inner { int x, y; };
        struct Outer { struct Inner a[3]; int tail; };
        static struct Outer o = { .a = { [1] = { .y = 7 } }, .tail = 9 };
        static int m[2][3] = { [1] = { [2] = 5 } };
        int main(void){ printf("%d %d %d %d %d\n", o.a[0].x, o.a[1].y, o.a[1].x, o.tail, m[1][2]); return 0; }
    """,
    "anonymous_members": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct S { int tag; union { int i; double d; struct { char a, b; }; }; };
        int main(void){ struct S s; s.tag = 1; s.i = 0x4142;
          printf("%d %d %d\n", s.tag, s.a, s.b);
          s.d = 2.5; printf("%.1f %d\n", s.d, (int)sizeof s); return 0; }
    """,
    "flexible_array": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct V { int n; int data[]; };
        int main(void){ struct V *v = malloc(sizeof(struct V) + 5*sizeof(int));
          v->n = 5; for(int i=0;i<5;i++) v->data[i]=i*3;
          int t=0; for(int i=0;i<v->n;i++) t+=v->data[i];
          printf("%d %d\n", t, (int)sizeof(struct V)); free(v); return 0; }
    """,
    "big_struct_copy": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct Big { char pad[300]; int tail; };
        static struct Big make(int n){ struct Big b; memset(&b, 0, sizeof b); b.pad[0]=(char)n; b.tail=n*2; return b; }
        static int look(struct Big b){ return b.pad[0] + b.tail; }
        int main(void){ struct Big x = make(7); struct Big y = x; y.tail = 1;
          printf("%d %d %d %d\n", look(x), x.tail, y.tail, (int)sizeof(struct Big)); return 0; }
    """,
    "struct_arrays": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct P { int x, y; };
        static struct P pts[4] = {{1,2},{3,4},{5,6},{7,8}};
        static int dot(struct P a, struct P b){ return a.x*b.x + a.y*b.y; }
        int main(void){ int t=0; for(int i=0;i<4;i++) t += dot(pts[i], pts[(i+1)%4]);
          struct P copy[4]; memcpy(copy, pts, sizeof pts);
          printf("%d %d %d\n", t, copy[3].y, (int)(sizeof pts / sizeof pts[0])); return 0; }
    """,
    "enums": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        enum E { NEG = -5, ZERO = 0, POS = 5, BIG = 1000000 };
        enum F : unsigned char { SMALL = 1, ALSO = 200 };
        int main(void){ enum E e = NEG; printf("%d %d %d %d %d\n", e, ZERO, POS, BIG, (int)sizeof(enum E));
          printf("%d %d\n", (int)ALSO, (int)sizeof(enum F)); return 0; }
    """,
    "overflow_and_shifts": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ int a = 2147483647; unsigned u = 4294967295u;
          printf("%d %u\n", a + 1, u + 1);
          for (int s = 0; s < 4; s++) printf("%d %u %d ", 1 << s, u >> s, -16 >> s);
          printf("\n");
          long long l = 1; printf("%lld %lld\n", l << 62, (l << 62) * 2);
          char c = 127; printf("%d %d\n", c + 1, (char)(c + 1)); return 0; }
    """,
    "goto_out_of_loops": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ int found = -1;
          for (int i = 0; i < 5; i++) for (int j = 0; j < 5; j++) if (i*j == 6) { found = i*10+j; goto done; }
          done: printf("%d\n", found);
          int k = 0;
          start: if (k < 3) { k++; goto start; }
          printf("%d\n", k); return 0; }
    """,
    "switch_fallthrough": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ for (int i = 0; i < 7; i++) { int n = 0;
            switch (i) { case 0: n += 1; case 1: n += 2; case 2: n += 4; break;
                         case 3: { int t = i * 2; n = t; } break;
                         case 4 ... 5: n = 99; break;
                         default: n = -1; }
            printf("%d ", n); } printf("\n");
          int x = 3; switch (x) { case 1: break; } printf("no default ok\n"); return 0; }
    """,
    "fn_returning_fn_ptr": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static int add1(int x){return x+1;}
        static int sub1(int x){return x-1;}
        static int (*pick(int n))(int){ return n ? add1 : sub1; }
        int main(void){ printf("%d %d\n", pick(1)(10), pick(0)(10)); return 0; }
    """,
    "comma_and_conditional": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ int a = 1, b = 2;
          int c = (a++, b++, a + b);
          printf("%d %d %d\n", a, b, c);
          const char *s = a > b ? "less" : "more";
          printf("%s\n", s);
          int *p = a ? &a : &b; printf("%d\n", *p);
          void *q = 0; printf("%d\n", q == NULL); return 0; }
    """,
    "va_list_parameter": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        #include <stdarg.h>
        static void emit(const char *fmt, va_list ap){ vprintf(fmt, ap); }
        static void wrap(const char *fmt, ...){ va_list ap; va_start(ap, fmt); emit(fmt, ap); va_end(ap); }
        int main(void){ wrap("%s %d %.2f\n", "x", 5, 2.5); wrap("%c%c\n", 'a', 'b'); return 0; }
    """,
    "sizeof_expressions": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct S { char c; long l; };
        int main(void){ int a[7]; struct S s;
          printf("%d %d %d %d\n", (int)sizeof a, (int)sizeof a[0], (int)sizeof s, (int)sizeof s.c);
          printf("%d %d %d\n", (int)sizeof(1+1), (int)sizeof('a'), (int)sizeof("ab"));
          printf("%d %d\n", (int)sizeof(char)*8, (int)(sizeof a / sizeof a[0])); return 0; }
    """,
    "pointer_casts": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ int v = 0x01020304; char *b = (char*)&v;
          printf("%d %d %d %d\n", b[0], b[1], b[2], b[3]);
          void *p = &v; int *q = p; printf("%x\n", *q);
          unsigned long addr = (unsigned long)&v; int *r = (int*)addr; printf("%d\n", *r == v);
          double d = 1.5; long *dl = (long*)&d; printf("%lx\n", (unsigned long)*dl); return 0; }
    """,
    "string_edge": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ char buf[64];
          snprintf(buf, sizeof buf, "%.*s|%-8s|%8.3s|", 3, "abcdef", "hi", "world");
          printf("%s\n", buf);
          printf("[%s]\n", "");
          char a[4] = "abc"; char b[4] = {'a','b','c',0};
          printf("%d %d\n", memcmp(a,b,4), strcmp(a,b));
          printf("%s\n", strrchr("a/b/c", '/')); return 0; }
    """,
    "recursive_struct": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct T { int v; struct T *l, *r; };
        static struct T *ins(struct T *n, int v){ if(!n){ n = malloc(sizeof *n); n->v=v; n->l=n->r=0; return n; }
          if (v < n->v) n->l = ins(n->l, v); else if (v > n->v) n->r = ins(n->r, v); return n; }
        static void walk(struct T *n){ if(!n) return; walk(n->l); printf("%d ", n->v); walk(n->r); }
        int main(void){ struct T *root = 0; int d[] = {5,3,8,1,4,7,9,2,6};
          for (unsigned i = 0; i < sizeof d / sizeof d[0]; i++) root = ins(root, d[i]);
          walk(root); printf("\n"); return 0; }
    """,
    "compound_literal_address": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct P { int a, b; };
        static int *keep;
        static int take(struct P *p){ return p->a + p->b; }
        int main(void){ printf("%d\n", take(&(struct P){3,4}));
          keep = (int[]){1,2,3}; printf("%d %d\n", keep[0], keep[2]);
          int *q = &(int){42}; printf("%d\n", *q); return 0; }
    """,
    "char_signedness": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ char c = -1; signed char s = -1; unsigned char u = 255;
          printf("%d %d %d\n", c, s, u);
          printf("%d %d\n", c == s, (int)(unsigned char)c);
          char arr[] = {-1, 0, 1}; int neg = 0;
          for (int i = 0; i < 3; i++) if (arr[i] < 0) neg++;
          printf("%d\n", neg); return 0; }
    """,
    "ternary_chain": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static const char *grade(int n){ return n >= 90 ? "A" : n >= 80 ? "B" : n >= 70 ? "C" : "F"; }
        int main(void){ int marks[] = {95, 85, 75, 65};
          for (int i = 0; i < 4; i++) printf("%s", grade(marks[i]));
          printf("\n"); return 0; }
    """,
    "linkage": r"""
        #include <stdio.h>
        extern int shared;
        int shared = 5;
        int tentative;
        int tentative;
        static int private_;
        static int bump(void){ extern int shared; return ++shared; }
        int main(void){ int a = shared; int b = bump();
          tentative = 3; private_ = 4;
          printf("%d %d %d %d %d\n", a, b, shared, tentative, private_); return 0; }
    """,
}


def program(name: str) -> str:
    import textwrap
    return textwrap.dedent(PROGRAMS[name])


class TestTheThreePathsAgree:
    @harness.needs("cc")
    @harness.cases("name", sorted(PROGRAMS))
    def test_interpreter_matches_the_host_compiler(self, name, tmp_path):
        source = program(name)
        want = _normalise(_host_run(source, tmp_path))
        got = _normalise(_interpret(_compile(source)))
        assert got == want

    @harness.needs("cc")
    @harness.cases("name", sorted(PROGRAMS))
    def test_the_c_backend_matches_the_host_compiler(self, name, tmp_path):
        source = program(name)
        want = _normalise(_host_run(source, tmp_path))
        got = _normalise(_through_c_backend(_compile(source), tmp_path))
        assert got == want


def _asmpython(*argv: str, cwd: Path) -> subprocess.CompletedProcess:
    """Run the CLI in a subprocess, against the package this test imported.

    THE PATH COMES FROM THE IMPORTED MODULE and not from the repository
    layout: the harness copies `src/` to a per-run directory and imports from
    there, so a test that computed the root from `__file__` would run the
    CLI from a DIFFERENT tree than the one it is testing.
    """
    import asmpython
    root = Path(asmpython.__file__).parents[1]
    env = dict(os.environ, PYTHONPATH=str(root))
    return subprocess.run([sys.executable, "-m", "asmpython", *argv],
                          capture_output=True, text=True, cwd=str(cwd),
                          env=env)


class TestTheDriverBuildsAndRuns:
    """The same thing again through `asmpython run`, so the wiring is tested
    and not only the pieces."""

    @harness.cases("flags,want", [
        ([], "hello 1\n"),
        (["-D", "N=7"], "hello 7\n"),
        (["-D", 'GREETING="hi"'], "hi 1\n"),
    ])
    def test_the_command_line_reaches_the_preprocessor(self, flags, want,
                                                       tmp_path):
        source = tmp_path / "prog.c"
        source.write_text(
            "#include <stdio.h>\n"
            "#ifndef GREETING\n#define GREETING \"hello\"\n#endif\n"
            "#ifndef N\n#define N 1\n#endif\n"
            "int main(void){ printf(\"%s %d\\n\", GREETING, N); return 0; }\n",
            encoding="utf-8")
        ran = _asmpython("run", str(source), *flags, cwd=tmp_path)
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout.endswith(want), ran.stdout

    def test_include_path_finds_the_projects_own_header(self, tmp_path):
        (tmp_path / "inc").mkdir()
        (tmp_path / "inc" / "mine.h").write_text(
            "#define ANSWER 42\n", encoding="utf-8")
        source = tmp_path / "prog.c"
        source.write_text(
            "#include <stdio.h>\n#include <mine.h>\n"
            "int main(void){ printf(\"%d\\n\", ANSWER); return 0; }\n",
            encoding="utf-8")
        ran = _asmpython("run", str(source), "-I", str(tmp_path / "inc"),
                         cwd=tmp_path)
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout.endswith("42\n"), ran.stdout
