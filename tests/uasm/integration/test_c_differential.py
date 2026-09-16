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

from uasm.diagnostics import DiagnosticSink, SourceFile
from uasm.frontends import c as c_frontend
from uasm.ir import verify
from uasm.ir.interpreter import Interpreter

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
    from uasm.backend import get, load_builtin
    from uasm.target import get as get_target
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

    # ── the third: the shapes that found the last six bugs ───────────────
    "restrict_and_deep_pointers": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        static void copyn(int *restrict d, const int *restrict s, int n){ for(int i=0;i<n;i++) d[i]=s[i]; }
        int main(void){ int a[4]={1,2,3,4}, b[4]; copyn(b,a,4);
          int x = 5; int *p = &x; int **q = &p; int ***r = &q;
          ***r = 9; printf("%d %d %d\n", b[3], x, **q); return 0; }
    """,
    "wide_strings": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        int main(void){ wchar_t w[] = L"abc"; unsigned short u[] = u"hi"; unsigned int U[] = U"xy";
          printf("%d %d %d %d\n", (int)w[0], (int)sizeof w, (int)u[1], (int)sizeof U);
          const char *j = "ab" "cd" "ef";
          printf("%s %d\n", j, (int)sizeof("ab" "cd")); return 0; }
    """,
    "alignas": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        struct S { _Alignas(16) char c; int i; };
        int main(void){ _Alignas(32) static int v = 3;
          printf("%d %d %d\n", (int)_Alignof(struct S), (int)sizeof(struct S), v);
          printf("%d\n", (int)((unsigned long)&v % 32) == 0); return 0; }
    """,
    "struct_with_string": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        struct Rec { char name[8]; int age; };
        static struct Rec people[2] = { { "ann", 30 }, { .name = "bob", .age = 40 } };
        int main(void){ for (int i = 0; i < 2; i++) printf("%s %d ", people[i].name, people[i].age);
          printf("\n%d %d\n", (int)sizeof people, people[0].name[3]); return 0; }
    """,
    "division_corners": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        int main(void){ int a = INT_MIN, b = -1;
          printf("%d %d\n", a / 2, a % 3);
          printf("%d %d %d\n", 7/2, -7/2, 7/-2);
          printf("%d %d %d\n", 7%2, -7%2, 7%-2);
          unsigned u = 4294967295u; printf("%u %u\n", u/3, u%3);
          long l = -9223372036854775807L - 1; printf("%ld\n", l / 2);
          (void)b; return 0; }
    """,
    "float_conversions": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        int main(void){ double d = 4294967296.5; float f = 1e20f;
          printf("%u %ld\n", (unsigned)1234.9, (long)d);
          printf("%d %d\n", (int)-0.9, (int)0.9);
          printf("%.1f %.1f\n", (double)(unsigned)4000000000u, (double)-1);
          printf("%d\n", (int)(f > 1e19f));
          unsigned char c = (unsigned char)200.7; printf("%d\n", c); return 0; }
    """,
    "big_switch": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        static int classify(int n){ switch(n){
          case 0: return 100; case 1: return 101; case 2: return 102; case 3: return 103;
          case 4: return 104; case 5: return 105; case 10: return 110; case 20: return 120;
          case 100: return 200; case -1: return 999; default: return -7; } }
        int main(void){ int ks[] = {0,3,5,10,20,100,-1,7};
          for (unsigned i=0;i<sizeof ks/sizeof ks[0];i++) printf("%d ", classify(ks[i]));
          printf("\n"); return 0; }
    """,
    "kr_definition": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        static int oldstyle(a, b) int a; char *b; { return a + (int)strlen(b); }
        static int noproto();
        static int noproto(void){ return 42; }
        int main(void){ printf("%d %d\n", oldstyle(5, "abc"), noproto()); return 0; }
    """,
    "void_and_exit_paths": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        static void nothing(void){ return; }
        static int deep(int n){ if (n == 0) return 0; return 1 + deep(n - 1); }
        int main(void){ nothing(); printf("%d\n", deep(100));
          for (int i = 0; i < 3; i++) { if (i == 1) continue; printf("%d", i); }
          printf("\n"); return 0; }
    """,
    "bit_manipulation": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        static unsigned rotl(unsigned v, int n){ return (v << n) | (v >> (32 - n)); }
        static int parity(unsigned v){ int p = 0; while (v) { p ^= v & 1u; v >>= 1; } return p; }
        int main(void){ unsigned x = 0x12345678u;
          printf("%x %x %d\n", rotl(x, 8), x ^ (x >> 16), parity(x));
          unsigned long long m = 0; for (int i = 0; i < 64; i += 8) m |= 1ULL << i;
          printf("%llx\n", m);
          printf("%d %d\n", __builtin_popcountll(m), (int)(m >> 56)); return 0; }
    """,
    "const_pointer_chain": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        static const char *const names[] = { "a", "bb", "ccc" };
        static int total(const char *const *p, int n){ int t = 0; for (int i=0;i<n;i++) t += (int)strlen(p[i]); return t; }
        int main(void){ printf("%d %s\n", total(names, 3), names[2]); return 0; }
    """,
    "self_referential": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        typedef struct Node Node;
        struct Node { int v; Node *next; };
        typedef struct { int (*op)(int, int); const char *name; } Entry;
        static int add(int a, int b){ return a + b; }
        static int mul(int a, int b){ return a * b; }
        static Entry table[] = { { add, "add" }, { mul, "mul" } };
        int main(void){ Node a = {1, 0}, b = {2, &a};
          printf("%d %d\n", b.v, b.next->v);
          for (int i = 0; i < 2; i++) printf("%s=%d ", table[i].name, table[i].op(3, 4));
          printf("\n"); return 0; }
    """,
    "sizeof_vla": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        static int probe(int n){ int a[n][3]; return (int)sizeof a + (int)sizeof a[0]; }
        int main(void){ printf("%d %d\n", probe(2), probe(5));
          int n = 4; printf("%d\n", (int)sizeof(char[n][n])); return 0; }
    """,
    "generic_qualified": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        #define kind(x) _Generic((x), int: 1, const int: 2, int *: 3, char *: 4, default: 0)
        int main(void){ const int c = 1; int i = 2; int *p = &i;
          printf("%d %d %d %d\n", kind(i), kind(c), kind(p), kind("s")); return 0; }
    """,
    "labels_and_blocks": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        int main(void){ int i = 0;
          { int i = 5; { int i = 9; printf("%d ", i); } printf("%d ", i); }
          printf("%d\n", i);
          switch (2) { case 1: { int x = 1; printf("%d", x); } break;
                       case 2: { int x = 2; printf("%d", x); } break; }
          printf("\n");
          goto end;
          printf("unreachable");
          end: ;
          return 0; }
    """,
    "preprocessor_in_anger": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        #define STR(x) #x
        #define XSTR(x) STR(x)
        #define CONCAT(a,b) a##b
        #define MAX(a,b) ((a) > (b) ? (a) : (b))
        #define LOG(fmt, ...) printf("[log] " fmt, ##__VA_ARGS__)
        #define VERSION 3
        int CONCAT(my, var) = 7;
        int main(void){ printf("%s %s %d\n", STR(VERSION), XSTR(VERSION), myvar);
          printf("%d %d\n", MAX(3, 5), MAX(-1, -2));
          LOG("plain\n");
          LOG("%d and %s\n", 42, "text");
          return 0; }
    """,
    "unsigned_char_math": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        int main(void){ unsigned char a = 200, b = 100;
          printf("%d %d %d\n", a + b, (unsigned char)(a + b), a * 2);
          signed char s = -128; printf("%d %d\n", -s, (signed char)-s);
          char buf[4] = {(char)0x80, (char)0xFF, 0x7F, 0};
          for (int i = 0; i < 3; i++) printf("%d %u ", buf[i], (unsigned char)buf[i]);
          printf("\n"); return 0; }
    """,
    "array_of_struct_init": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        struct P { int x, y; const char *n; };
        static struct P a[] = { {1,2,"one"}, {3,4,"two"}, {5,6,"three"} };
        static int counts[3][3] = { {1}, {0,2}, {0,0,3} };
        int main(void){ printf("%d ", (int)(sizeof a / sizeof a[0]));
          for (unsigned i = 0; i < sizeof a / sizeof a[0]; i++) printf("%s:%d ", a[i].n, a[i].x*a[i].y);
          printf("\n");
          for (int i = 0; i < 3; i++) for (int j = 0; j < 3; j++) printf("%d", counts[i][j]);
          printf("\n"); return 0; }
    """,
    "long_expression": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        int main(void){ int t = 1+2+3+4+5+6+7+8+9+10+11+12+13+14+15+16+17+18+19+20
          +21+22+23+24+25+26+27+28+29+30+31+32+33+34+35+36+37+38+39+40;
          double d = 1.0*2.0*3.0/4.0+5.0-6.0*7.0/8.0+9.0-10.0;
          printf("%d %.4f\n", t, d);
          int a=1,b=2,c=3; printf("%d\n", a<b ? b<c ? 1 : 2 : c<a ? 3 : 4); return 0; }
    """,
    "memcmp_structs": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>
        #include <limits.h>

        struct K { int a; int b; };
        int main(void){ struct K x = {1,2}, y = {1,2}, z = {1,3};
          printf("%d %d\n", memcmp(&x,&y,sizeof x) == 0, memcmp(&x,&z,sizeof x) == 0);
          char big[200], other[200];
          memset(big, 'q', sizeof big); memcpy(other, big, sizeof big);
          printf("%d %d\n", memcmp(big, other, sizeof big), big[199]);
          other[150] = 'z'; printf("%d\n", memcmp(big, other, sizeof big) < 0); return 0; }
    """,

    # ── the fourth: whole programs, because that is what C is for ────────
    "vtable_pattern": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct Shape; 
        struct Ops { double (*area)(const struct Shape *); const char *name; };
        struct Shape { const struct Ops *ops; double a, b; };
        static double rect(const struct Shape *s){ return s->a * s->b; }
        static double tri(const struct Shape *s){ return s->a * s->b / 2.0; }
        static const struct Ops RECT = { rect, "rect" };
        static const struct Ops TRI = { tri, "tri" };
        int main(void){ struct Shape shapes[] = { {&RECT, 3, 4}, {&TRI, 3, 4} };
          for (unsigned i = 0; i < sizeof shapes / sizeof shapes[0]; i++)
            printf("%s %.2f\n", shapes[i].ops->name, shapes[i].ops->area(&shapes[i]));
          return 0; }
    """,
    "tokenizer": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ char line[] = "alpha,beta,,gamma";
          char *tok = strtok(line, ",");
          while (tok) { printf("[%s]", tok); tok = strtok(NULL, ","); }
          printf("\n");
          char words[] = "  the   quick brown  ";
          for (char *w = strtok(words, " "); w; w = strtok(NULL, " ")) printf("<%s>", w);
          printf("\n"); return 0; }
    """,
    "sort_structs": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        struct Person { char name[12]; int age; };
        static int by_age(const void *a, const void *b){
          const struct Person *x = a, *y = b; return (x->age > y->age) - (x->age < y->age); }
        static int by_name(const void *a, const void *b){
          return strcmp(((const struct Person*)a)->name, ((const struct Person*)b)->name); }
        int main(void){ struct Person p[] = {{"carol",31},{"alice",25},{"bob",40},{"dave",25}};
          int n = (int)(sizeof p / sizeof p[0]);
          qsort(p, n, sizeof p[0], by_age);
          for (int i=0;i<n;i++) printf("%s:%d ", p[i].name, p[i].age); printf("\n");
          qsort(p, n, sizeof p[0], by_name);
          for (int i=0;i<n;i++) printf("%s ", p[i].name); printf("\n"); return 0; }
    """,
    "expression_evaluator": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static const char *cursor;
        static long parse_expr(void);
        static void skip(void){ while (*cursor == ' ') cursor++; }
        static long parse_atom(void){ skip();
          if (*cursor == '(') { cursor++; long v = parse_expr(); skip(); if (*cursor==')') cursor++; return v; }
          int neg = 0; if (*cursor == '-') { neg = 1; cursor++; }
          long v = 0; while (*cursor >= '0' && *cursor <= '9') { v = v*10 + (*cursor - '0'); cursor++; }
          return neg ? -v : v; }
        static long parse_term(void){ long v = parse_atom();
          for (;;) { skip(); if (*cursor=='*'){cursor++; v *= parse_atom();}
            else if (*cursor=='/'){cursor++; long d = parse_atom(); if (d) v /= d;}
            else return v; } }
        static long parse_expr(void){ long v = parse_term();
          for (;;) { skip(); if (*cursor=='+'){cursor++; v += parse_term();}
            else if (*cursor=='-'){cursor++; v -= parse_term();} else return v; } }
        int main(void){ const char *tests[] = {"1+2*3", "(1+2)*3", "10/3", "-4 + 5", "2*(3+4)-5"};
          for (unsigned i=0;i<sizeof tests/sizeof tests[0];i++){ cursor = tests[i];
            printf("%s = %ld\n", tests[i], parse_expr()); } return 0; }
    """,
    "matrix": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        #define N 4
        static void mul(double a[N][N], double b[N][N], double out[N][N]){
          for (int i=0;i<N;i++) for (int j=0;j<N;j++){ double s=0;
            for (int k=0;k<N;k++) s += a[i][k]*b[k][j]; out[i][j]=s; } }
        int main(void){ double a[N][N], b[N][N], c[N][N];
          for (int i=0;i<N;i++) for (int j=0;j<N;j++){ a[i][j] = i+j; b[i][j] = (i==j); }
          mul(a,b,c);
          for (int i=0;i<N;i++){ for (int j=0;j<N;j++) printf("%.1f ", c[i][j]); printf("\n"); }
          return 0; }
    """,
    "conditional_compilation": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        #define MODE 2
        #if MODE == 1
        static int pick(void){ return 1; }
        #elif MODE == 2
        static int pick(void){ return 2; }
        #else
        static int pick(void){ return 0; }
        #endif
        #if defined(MODE) && MODE > 1
        #define EXTRA 10
        #else
        #define EXTRA 0
        #endif
        int main(void){ printf("%d %d\n", pick(), EXTRA);
        #ifdef NOTHING
          printf("never\n");
        #endif
          return 0; }
    """,
    "static_inline": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static inline int clampi(int v, int lo, int hi){ return v < lo ? lo : v > hi ? hi : v; }
        static inline unsigned hash(const char *s){ unsigned h = 5381;
          while (*s) h = h * 33u + (unsigned char)*s++; return h; }
        int main(void){ printf("%d %d %d\n", clampi(5,0,10), clampi(-1,0,10), clampi(99,0,10));
          printf("%u %u\n", hash("abc"), hash("")); return 0; }
    """,
    "long_long_math": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ long long a = 1234567890123456789LL, b = 987654321LL;
          printf("%lld %lld %lld %lld\n", a+b, a-b, a/b, a%b);
          unsigned long long u = 18446744073709551615ULL;
          printf("%llu %llu %llu\n", u, u/3, u>>7);
          printf("%lld\n", (long long)(a * 3));
          long long neg = -a; printf("%lld %lld\n", neg/b, neg%b); return 0; }
    """,
    "multidim_vla": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static long trace(int n, int m[n][n]){ long t=0; for(int i=0;i<n;i++) t += m[i][i]; return t; }
        int main(void){ int n = 4; int grid[n][n];
          for(int i=0;i<n;i++) for(int j=0;j<n;j++) grid[i][j] = i*n+j;
          printf("%ld %d\n", trace(n, grid), grid[3][2]);
          for (int k = 1; k <= 3; k++) { int tmp[k]; for (int i=0;i<k;i++) tmp[i]=k; printf("%d", tmp[k-1]); }
          printf("\n"); return 0; }
    """,
    "assignment_chains": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ int a, b, c; a = b = c = 7;
          printf("%d %d %d\n", a, b, c);
          int arr[3] = {0}; int i = 0;
          arr[i] = i = 2;
          printf("%d %d %d %d\n", arr[0], arr[1], arr[2], i);
          int x = 1; x += x += 3; printf("%d\n", x);
          double d = 1; d *= d += 2; printf("%.1f\n", d); return 0; }
    """,
    "void_pointer_arith": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ int a[4] = {10,20,30,40}; char *p = (char*)a;
          p += 2 * sizeof(int);
          printf("%d\n", *(int*)p);
          void *v = a; printf("%d\n", *((int*)v + 3));
          printf("%ld\n", (char*)&a[3] - (char*)&a[0]); return 0; }
    """,
    "trailing_commas": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        enum E { A, B, C, };
        static int nums[] = { 1, 2, 3, };
        struct S { int x, y; };
        static struct S s = { .x = 1, .y = 2, };
        int main(void){ printf("%d %d %d %d\n", C, (int)(sizeof nums/sizeof nums[0]), s.x, s.y); return 0; }
    """,
    "type_punning": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        union Bits { float f; unsigned u; };
        union DBits { double d; unsigned long u; };
        int main(void){ union Bits b; b.f = 1.0f; printf("%x\n", b.u);
          union DBits d; d.d = -2.0; printf("%lx\n", d.u);
          d.u = 0x3FF0000000000000UL; printf("%.1f\n", d.d);
          float f = 2.5f; unsigned raw; memcpy(&raw, &f, sizeof raw); printf("%x\n", raw); return 0; }
    """,
    "array_vs_pointer": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static char arr[16] = "hello";
        static char *ptr = "hello";
        static int takes(char a[16]){ return (int)sizeof a; }
        int main(void){ printf("%d %d %d\n", (int)sizeof arr, (int)sizeof ptr, takes(arr));
          printf("%d %d\n", (int)sizeof "hello", (int)strlen("hello"));
          char (*pa)[16] = &arr; printf("%d %c\n", (int)sizeof *pa, (*pa)[1]); return 0; }
    """,
    "deep_recursion": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static long ack(int m, long n){ if (m == 0) return n + 1;
          if (n == 0) return ack(m - 1, 1); return ack(m - 1, ack(m, n - 1)); }
        static long sumto(long n){ return n == 0 ? 0 : n + sumto(n - 1); }
        int main(void){ printf("%ld %ld %ld\n", ack(1, 5), ack(2, 3), sumto(200)); return 0; }
    """,
    "printf_loop": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ const char *fmts[] = {"%d|", "%5d|", "%-5d|", "%+d|", "%x|", "%o|"};
          for (unsigned i = 0; i < sizeof fmts / sizeof fmts[0]; i++) printf(fmts[i], 42);
          printf("\n");
          for (int i = 0; i < 5; i++) printf("%*.*f|", 8, i, 3.14159265);
          printf("\n");
          for (double v = 0.5; v < 1e7; v *= 12.3) printf("%g|", v);
          printf("\n"); return 0; }
    """,
    "bitfield_union": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        union Reg { unsigned raw; struct { unsigned lo : 8, mid : 8, hi : 16; }; };
        int main(void){ union Reg r; r.raw = 0xAABBCCDD;
          printf("%x %x %x\n", r.lo, r.mid, r.hi);
          r.mid = 0x11; printf("%x\n", r.raw);
          struct { signed s : 4; unsigned u : 4; } p = { -1, 15 };
          printf("%d %u %d\n", p.s, p.u, (int)sizeof p); return 0; }
    """,
    "string_builder": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        int main(void){ char out[128]; size_t used = 0;
          const char *parts[] = {"alpha", "beta", "gamma", "delta"};
          for (unsigned i = 0; i < 4; i++) {
            int n = snprintf(out + used, sizeof out - used, "%s%s", i ? ", " : "", parts[i]);
            if (n > 0) used += (size_t)n; }
          printf("%s (%d)\n", out, (int)used);
          char small[8]; int need = snprintf(small, sizeof small, "%s-%s", parts[0], parts[1]);
          printf("%s %d\n", small, need); return 0; }
    """,
    "goto_cleanup": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        static int work(int fail){ int *a = NULL, *b = NULL, rc = 0;
          a = malloc(4 * sizeof *a); if (!a) { rc = 1; goto out; }
          if (fail == 1) { rc = 2; goto out_a; }
          b = malloc(4 * sizeof *b); if (!b) { rc = 3; goto out_a; }
          if (fail == 2) { rc = 4; goto out_b; }
          a[0] = 1; b[0] = 2; rc = a[0] + b[0];
        out_b: free(b);
        out_a: free(a);
        out: return rc; }
        int main(void){ printf("%d %d %d\n", work(0), work(1), work(2)); return 0; }
    """,
    "enum_switch_table": r"""
        #include <stdio.h>
        #include <string.h>
        #include <stdlib.h>

        enum Op { OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_COUNT };
        static const char *const names[OP_COUNT] = { "add", "sub", "mul", "div" };
        static int apply(enum Op o, int a, int b){
          switch (o) { case OP_ADD: return a+b; case OP_SUB: return a-b;
                       case OP_MUL: return a*b; case OP_DIV: return b ? a/b : 0;
                       case OP_COUNT: default: return -1; } }
        int main(void){ for (int o = 0; o < OP_COUNT; o++)
            printf("%s(%d,%d)=%d ", names[o], 12, 4, apply((enum Op)o, 12, 4));
          printf("\n"); return 0; }
    """,

    # ── the C23 headers that arrived last ────────────────────────────────
    "inttypes_and_locale": r"""
        #include <stdio.h>
        #include <inttypes.h>
        #include <locale.h>
        #include <fenv.h>
        int main(void) {
            int64_t a = 1234567890123L;
            uint64_t b = 18446744073709551615UL;
            printf("%" PRId64 " %" PRIu64 " %" PRIx64 "\n", a, b, (uint64_t)255);
            printf("%jd %ju\n", (intmax_t)-7, (uintmax_t)7);
            imaxdiv_t d = imaxdiv(17, 5);
            printf("%ld %ld %ld\n", (long)d.quot, (long)d.rem, (long)imaxabs(-9));
            printf("%s %s\n", setlocale(LC_ALL, "C"), localeconv()->decimal_point);
            printf("%d\n", fegetround() == FE_TONEAREST);
            printf("%" PRIdPTR " %" PRIuMAX "\n", (intptr_t)-3, (uintmax_t)9);
            return 0;
        }
    """,
    "wide_and_generic": r"""
        #include <stdio.h>
        #include <wchar.h>
        #include <wctype.h>
        #include <tgmath.h>
        int main(void) {
            wchar_t a[16], b[] = L"hello";
            wcscpy(a, b); wcscat(a, L" wide");
            printf("%d %d %d\n", (int)wcslen(a), (int)a[0], wcscmp(a, L"hello wide"));
            printf("%d %d\n", (int)(wcschr(a, L'w') - a), (int)(wcsstr(a, L"wide") - a));
            /* The isw* functions answer "nonzero", and glibc's is a bitmask. */
            printf("%d %d %d %d\n", iswalpha(L'q') != 0, iswdigit(L'7') != 0,
                   (int)towupper(L'x'), iswspace(L'\t') != 0);
            wchar_t c[8]; wmemset(c, L'z', 4); c[4] = 0;
            printf("%d %d %d\n", (int)wcslen(c), wmemcmp(c, L"zzzz", 4), (int)c[3]);
            float f = 2.0f; double d = 2.0;
            printf("%.6f %.6f %.6f\n", (double)sqrt(f), sqrt(d), (double)pow(f, 3.0f));
            printf("%.6f %.6f %.6f\n", (double)fabs(-1.5f), floor(-1.5), fmod(7.5, 2.0));
            return 0;
        }
    """,

    "duffs_device": r"""
        #include <stdio.h>
        /* A `case` label inside a `do` loop inside a `switch`. It is legal C
           and it is the shape that proves `switch` really is a jump into a
           statement rather than a chain of comparisons around one. */
        static void copy(char *to, const char *from, int count) {
            int n = (count + 7) / 8;
            switch (count % 8) {
            case 0: do { *to++ = *from++;
            case 7:      *to++ = *from++;
            case 6:      *to++ = *from++;
            case 5:      *to++ = *from++;
            case 4:      *to++ = *from++;
            case 3:      *to++ = *from++;
            case 2:      *to++ = *from++;
            case 1:      *to++ = *from++;
                    } while (--n > 0);
            }
        }
        int main(void) {
            char dst[32] = {0};
            copy(dst, "duffs device works", 18);
            printf("%s %d\n", dst, (int)sizeof dst);
            return 0;
        }
    """,

    "setjmp_and_longjmp": r"""
        #include <stdio.h>
        #include <stdlib.h>
        #include <setjmp.h>
        /* THE FRAMES UNWIND THEMSELVES here and a real implementation
           restores a stack pointer, so this is the program that says
           whether the two mean the same thing. Every case that could tell
           them apart is in it: a jump out of a recursion, a jump within one
           frame, a jump out of a `qsort` callback -- through the library's
           own frames -- and `longjmp(env, 0)`, which answers 1. */
        static jmp_buf outer;

        static int depth(int n, jmp_buf *back)
        {
            jmp_buf here;
            int r = setjmp(here);
            if (r) { printf("caught at %d with %d\n", n, r); return n * 100 + r; }
            if (n == 0) longjmp(*back, 7);
            printf("down %d\n", n);
            { int got = depth(n - 1, &here); printf("returned %d at %d\n", got, n); }
            return -1;
        }

        static int guarded(int c)
        {
            jmp_buf local;
            printf("guarded %d\n", c);     /* a call BEFORE the setjmp */
            if (c) {
                int v = setjmp(local);
                if (v) return v;
                longjmp(local, 5);         /* and a jump within one frame */
            }
            return -1;
        }

        static int cmp(const void *a, const void *b)
        {
            int x = *(const int *)a, y = *(const int *)b;
            if (x == 99 || y == 99) longjmp(outer, 3);
            return x - y;
        }

        int main(void)
        {
            int arr[5] = { 4, 2, 99, 1, 3 };
            int r;
            printf("%d\n", guarded(1));
            printf("%d\n", depth(2, &outer));
            r = setjmp(outer);
            if (r == 0) {
                qsort(arr, 5, sizeof(int), cmp);
                printf("sorted without jumping\n");
            } else {
                printf("jumped out of qsort with %d\n", r);
            }
            r = setjmp(outer);
            if (r == 0) longjmp(outer, 0);
            printf("zero became %d\n", r);
            return 0;
        }
    """,

    "setjmp_keeps_what_the_frame_had": r"""
        #include <stdio.h>
        #include <setjmp.h>
        /* A `volatile` local is the one C promises survives, so it is the
           one a portable program uses and the one to compare. */
        static jmp_buf e;
        static void thrower(int n) { if (n > 2) longjmp(e, n); }
        int main(void)
        {
            volatile int seen = 0;
            volatile int i;
            int r = setjmp(e);
            if (r) { printf("jump %d after %d\n", r, seen); return 0; }
            for (i = 0; i < 5; i++) { seen = i; thrower(i); }
            printf("no jump\n");
            return 0;
        }
    """,
    "sscanf_conversions": r"""
        #include <stdio.h>
        #include <string.h>
        /* THE SCANNER, OVER A STRING, so it needs no host service and can
           be checked on every path. Every conversion class is here: the
           bases, the width, the suppression, the scanset and the two ways
           a conversion can fail. */
        int main(void) {
            int a = 0, b = 0, n = 0;
            unsigned u = 0;
            long l = 0;
            double d = 0;
            char word[16] = {0}, rest[16] = {0}, ch = 0;
            n = sscanf("12 -34 0x1f 077", "%d %d %x %lo", &a, &b, &u, &l);
            printf("%d: %d %d %u %ld\n", n, a, b, u, l);
            n = sscanf("  hello world", "%5s %c", word, &ch);
            printf("%d: [%s] [%c]\n", n, word, ch);
            n = sscanf("3.5e2xyz", "%lf%2s", &d, rest);
            printf("%d: %g [%s]\n", n, d, rest);
            n = sscanf("42abc", "%*d%[a-c]", word);
            printf("%d: [%s]\n", n, word);
            n = sscanf("ab", "%d", &a);
            printf("%d\n", n);
            n = sscanf("", "%d", &a);
            printf("%d\n", n);
            n = sscanf("7 8", "%d%n %d", &a, &l, &b);
            printf("%d: %d %ld %d\n", n, a, l, b);
            n = sscanf("1,2;3", "%d,%d;%d", &a, &b, &u);
            printf("%d: %d %d %u\n", n, a, b, u);
            return 0;
        }
    """,

    "strtod_is_exact": r"""
        #include <stdio.h>
        #include <stdlib.h>
        #include <string.h>
        /* THE ROUND TRIP, BOTH WAYS. `%.17g` writes enough digits to name
           the value and `strtod` has to land back on exactly it -- which is
           only true if both directions are correctly rounded, and is what
           the bignum in each of them is for. The awkward ones are here by
           name: `1e23` is the classic off-by-one-ulp, and the subnormals
           are where a scaled conversion stops working entirely. */
        static void trip(double v) {
            char buf[64];
            double back;
            snprintf(buf, sizeof buf, "%.17g", v);
            back = strtod(buf, NULL);
            printf("%s %d\n", buf, back == v);
        }
        int main(void) {
            trip(1.0);
            trip(0.1);
            trip(1e23);
            trip(1e-300);
            trip(123456789.0 / 7.0);
            trip(3.141592653589793);
            printf("%.17g\n", strtod("1e23", NULL));
            printf("%.17g\n", strtod("9007199254740993", NULL));
            printf("%.17g\n", strtod("2.2250738585072011e-308", NULL));
            printf("%a %a %a\n", strtod("1e-323", NULL),
                   strtod("0x1p-1074", NULL), strtod("255.5", NULL));
            printf("%g %g\n", strtod("nope", NULL), strtod("  12.5rest", NULL));
            printf("%d\n", strtod("inf", NULL) > 1e308);
            return 0;
        }
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


def _uasm(*argv: str, cwd: Path, stdin_text: str | None = None,
               extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run the CLI in a subprocess, against the package this test imported.

    THE PATH COMES FROM THE IMPORTED MODULE and not from the repository
    layout: the harness copies `src/` to a per-run directory and imports from
    there, so a test that computed the root from `__file__` would run the
    CLI from a DIFFERENT tree than the one it is testing.

    STANDARD INPUT AND THE ENVIRONMENT ARE ARGUMENTS because a program that
    reads one or asks about the other has to be given them from outside: the
    host services read the real descriptor and the real environment, which
    is the point of them.
    """
    import uasm
    root = Path(uasm.__file__).parents[1]
    env = dict(os.environ, PYTHONPATH=str(root), **(extra_env or {}))
    return subprocess.run([sys.executable, "-m", "uasm", *argv],
                          capture_output=True, text=True, cwd=str(cwd),
                          env=env, input=stdin_text)


#: A program small enough to go through a machine backend quickly, and
#: written against the floor alone so that no backend needs a C library.
SMALL = """\
extern long plat_write(long fd, const void *b, long n);
static void say(const char *s) { long n = 0; while (s[n]) n++; plat_write(1, s, n); }
int main(void) {
    int total = 0;
    for (int i = 1; i <= 10; i++) total += i * i;
    char digits[8], out[8];
    int k = 0, j = 0, v = total;
    while (v) { digits[k++] = (char)('0' + v % 10); v /= 10; }
    while (k) out[j++] = digits[--k];
    out[j++] = '\\n';
    plat_write(1, out, j);
    say("done\\n");
    return 0;
}
"""


class TestTheOtherBackendsToo:
    """The point of a language-independent IR is that a frontend does not have
    to know which backend is downstream. So one C program is put through the
    machine backend and the JVM one, and both must print what `cc` prints.

    A SMALL PROGRAM ON PURPOSE. These paths are slow -- one encodes x86-64
    instructions and writes an ELF object, the other builds a class file and
    a jar -- and what is checked is that a C frontend's IR is ordinary IR,
    not that `printf` works again.
    """

    @harness.needs("cc")
    def test_x86_64(self, tmp_path):
        want = _host_run(SMALL, tmp_path)
        source = tmp_path / "small.c"
        source.write_text(SMALL, encoding="utf-8")
        exe = tmp_path / "small.exe"
        built = _uasm("build", str(source), "--backend", "x86-64",
                           "--target", "x86_64-linux", "-o", str(exe),
                           cwd=tmp_path)
        if built.returncode != 0:
            harness.skip(f"the x86-64 path is unavailable here: "
                         f"{(built.stderr or built.stdout)[:160]}")
        ran = subprocess.run([str(exe)], capture_output=True, text=True)
        assert (ran.returncode, ran.stdout) == want

    @harness.needs("java")
    def test_jvm(self, tmp_path):
        want = _host_run(SMALL, tmp_path)
        source = tmp_path / "small.c"
        source.write_text(SMALL, encoding="utf-8")
        jar = tmp_path / "small.jar"
        built = _uasm("build", str(source), "--backend", "jvm",
                           "-o", str(jar), cwd=tmp_path)
        assert built.returncode == 0, built.stderr or built.stdout
        ran = subprocess.run([shutil.which("java"), "-jar", str(jar)],
                             capture_output=True, text=True)
        assert (ran.returncode, ran.stdout) == want

    def test_pybc_refuses_for_the_right_reason(self, tmp_path):
        """`pybc` is the one backend that is not language-independent: it
        hands the ORIGINAL SOURCE to the host CPython. Told a C module it used
        to answer `invalid syntax (prog.c, line 1)`, which names the wrong
        problem entirely."""
        source = tmp_path / "small.c"
        source.write_text(SMALL, encoding="utf-8")
        built = _uasm("build", str(source), "--backend", "pybc",
                           "-o", str(tmp_path / "small.pyc"), cwd=tmp_path)
        assert built.returncode != 0
        said = built.stdout + built.stderr
        assert "can only be used with the `python` frontend" in said, said


class TestTheDriverBuildsAndRuns:
    """The same thing again through `uasm run`, so the wiring is tested
    and not only the pieces."""

    @harness.cases("flags,want", [
        ([], "hello 1\n"),
        (["--define", "N=7"], "hello 7\n"),
        (["--define", 'GREETING="hi"'], "hi 1\n"),
        (["--c:define", "N=7", "--c:define", 'GREETING="hi"'], "hi 7\n"),
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
        ran = _uasm("run", str(source), *flags, cwd=tmp_path)
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout.endswith(want), ran.stdout

    def test_a_flag_the_chosen_frontend_does_not_take_says_who_does(
            self, tmp_path):
        """`--include-path` is the C frontend's and nobody else's. Handed to a
        Python build it is an error that names the frontend that takes it --
        not something ignored, because compiling without it produces
        something that is not what was asked for."""
        source = tmp_path / "prog.py"
        source.write_text("def main() -> int:\n    return 0\n",
                          encoding="utf-8")
        ran = _uasm("run", str(source), "--include-path", str(tmp_path),
                         cwd=tmp_path)
        said = ran.stdout + ran.stderr
        assert ran.returncode != 0
        assert "the python frontend does not take --include-path" in said, said
        assert "pass --frontend c" in said, said

    def test_include_path_finds_the_projects_own_header(self, tmp_path):
        (tmp_path / "inc").mkdir()
        (tmp_path / "inc" / "mine.h").write_text(
            "#define ANSWER 42\n", encoding="utf-8")
        source = tmp_path / "prog.c"
        source.write_text(
            "#include <stdio.h>\n#include <mine.h>\n"
            "int main(void){ printf(\"%d\\n\", ANSWER); return 0; }\n",
            encoding="utf-8")
        ran = _uasm("run", str(source), "--include-path",
                         str(tmp_path / "inc"), cwd=tmp_path)
        assert ran.returncode == 0, ran.stderr
        assert ran.stdout.endswith("42\n"), ran.stdout


# ── the host services ───────────────────────────────────────────────────────
#
# THE SAME THREE PATHS, run differently. Everything above this line is a
# program that needs nothing but the floor, so the interpreter can run it
# in-process and the comparison is a string. A program that reads `stdin`,
# opens a file or asks for the command line needs a process to have those
# things, so these go through the CLI in a subprocess -- with a working
# directory of their own, a fixed line of input and two arguments.
#
# WHAT THE ORACLE IS DOING HERE IS DIFFERENT IN KIND from the programs above.
# There, `cc` and this frontend compile the same text and the library is the
# only difference; here the library underneath is a real one on one side and
# `objects/hostsvc.py` on the other, and the point is that a program cannot
# tell. `fseek` past a read-ahead buffer, `%[^,]` stopping where it should,
# `mktime` normalising the 32nd of January: each is somewhere the two could
# disagree and does not.

#: What every program below is given on its standard input.
HOST_INPUT = "first line\n11 31\nlast\n"

#: And the words after the program's name.
HOST_ARGS = ("alpha", "beta")

#: And one variable in its environment, so `getenv` has something to find.
HOST_ENV = {"ASMPY_DIFFERENTIAL": "set-by-the-test"}

HOST_PROGRAMS: dict[str, str] = {
    "a_file_round_trip": r"""
        #include <stdio.h>
        #include <string.h>
        int main(void) {
            FILE *f = fopen("round.txt", "w");
            char buf[64];
            int a = 0, b = 0;
            long at;
            if (!f) { printf("no file\n"); return 1; }
            fprintf(f, "%d %d\nsecond line\nthird\n", 17, 23);
            fputc('!', f);
            fputs("tail", f);
            fclose(f);

            f = fopen("round.txt", "r");
            if (!f) { printf("no reopen\n"); return 1; }
            printf("%d\n", fscanf(f, "%d %d", &a, &b));
            printf("%d %d\n", a, b);
            fgets(buf, sizeof buf, f);
            fgets(buf, sizeof buf, f);
            printf("[%s]", buf);
            at = ftell(f);
            printf("at %ld eof %d\n", at, feof(f));
            fseek(f, 0, SEEK_SET);
            printf("%d bytes back: [%.5s]\n", (int)fread(buf, 1, 5, f), buf);
            fseek(f, -4, SEEK_END);
            fgets(buf, sizeof buf, f);
            printf("end [%s]\n", buf);
            printf("%d\n", fgetc(f));
            printf("eof %d\n", feof(f) != 0);
            fclose(f);

            f = fopen("round.txt", "a");
            fputs("+more", f);
            fclose(f);
            f = fopen("round.txt", "r");
            at = 0;
            while (fgetc(f) != EOF) at++;
            printf("size %ld\n", at);
            fclose(f);

            printf("remove %d\n", remove("round.txt"));
            printf("gone %d\n", fopen("round.txt", "r") == NULL);
            printf("missing %d\n", fopen("not-there.txt", "r") == NULL);
            return 0;
        }
    """,

    "standard_input": r"""
        #include <stdio.h>
        #include <string.h>
        int main(void) {
            char line[64];
            int a = 0, b = 0, c;
            if (fgets(line, sizeof line, stdin)) printf("one [%s]", line);
            printf("%d\n", scanf("%d %d", &a, &b));
            printf("%d\n", a + b);
            c = getchar();
            printf("after %d\n", c);
            ungetc(c, stdin);
            if (fgets(line, sizeof line, stdin)) printf("two [%s]", line);
            printf("%d %d\n", getchar() == EOF, feof(stdin) != 0);
            return 0;
        }
    """,

    "the_calendar": r"""
        #include <stdio.h>
        #include <time.h>
        int main(void) {
            time_t fixed = 1700000000;
            struct tm *tm = gmtime(&fixed);
            char out[128];
            struct tm copy;
            printf("%04d-%02d-%02d %02d:%02d:%02d wday %d yday %d\n",
                   tm->tm_year + 1900, tm->tm_mon + 1, tm->tm_mday,
                   tm->tm_hour, tm->tm_min, tm->tm_sec,
                   tm->tm_wday, tm->tm_yday);
            strftime(out, sizeof out, "%Y-%m-%dT%H:%M:%S %a %A %b %B %j %p %I",
                     tm);
            printf("%s\n", out);
            strftime(out, sizeof out, "%F %T %D %R %e %C %y %u %w %%", tm);
            printf("%s\n", out);
            printf("%s", asctime(tm));
            printf("%ld\n", (long)mktime(tm));
            /* THE 32ND OF JANUARY, which `mktime` has to turn into the
               first of February -- normalising is most of what it is for. */
            copy = *tm;
            copy.tm_year = 100; copy.tm_mon = 0; copy.tm_mday = 32;
            copy.tm_hour = 25; copy.tm_min = 0; copy.tm_sec = 0;
            printf("%ld\n", (long)mktime(&copy));
            printf("%04d-%02d-%02d %02d\n", copy.tm_year + 1900,
                   copy.tm_mon + 1, copy.tm_mday, copy.tm_hour);
            {
                time_t early = 0, negative = -86399;
                printf("%s", asctime(gmtime(&early)));
                printf("%s", asctime(gmtime(&negative)));
            }
            printf("now %d\n", time(NULL) > 1700000000);
            printf("clock %d\n", clock() >= 0);
            printf("%.0f\n", difftime(100, 40));
            return 0;
        }
    """,

    "the_environment": r"""
        #include <stdio.h>
        #include <stdlib.h>
        #include <string.h>
        int main(void) {
            const char *v = getenv("ASMPY_DIFFERENTIAL");
            printf("[%s]\n", v ? v : "(unset)");
            printf("%d\n", getenv("ASMPY_NOT_SET_AT_ALL") == NULL);
            printf("%d\n", (int)strlen(getenv("ASMPY_DIFFERENTIAL")));
            return 0;
        }
    """,

    "the_command_line": r"""
        #include <stdio.h>
        #include <string.h>
        int main(int argc, char **argv) {
            int i;
            printf("%d\n", argc);
            /* NOT argv[0], which is the program's own name and is a
               different string on each path by definition. */
            for (i = 1; i < argc; i++) printf("%d [%s]\n", i, argv[i]);
            printf("%d\n", argv[argc] == NULL);
            printf("%d\n", (int)strlen(argv[1]));
            return 0;
        }
    """,
}


def _host_service_run(source: str, where: Path) -> tuple[int, str]:
    """The oracle: `cc`, in its own directory, with the same input."""
    where.mkdir(parents=True, exist_ok=True)
    src = where / "host.c"
    exe = where / "host.exe"
    src.write_text(HOST_PRELUDE + source, encoding="utf-8")
    built = subprocess.run(
        [HAS_CC, "-std=c11", "-w", "-o", str(exe), str(src), "-lm"],
        capture_output=True, text=True)
    assert built.returncode == 0, f"the host compiler refused it:\n{built.stderr}"
    ran = subprocess.run([str(exe), *HOST_ARGS], capture_output=True, text=True,
                         input=HOST_INPUT, cwd=str(where),
                         env=dict(os.environ, **HOST_ENV))
    return ran.returncode, ran.stdout


class TestTheHostServicesAgreeToo:
    """A program that reads, opens a file, asks the time or looks at its
    own command line, three ways -- and the same answer each time."""

    @harness.needs("cc")
    @harness.cases("name", sorted(HOST_PROGRAMS))
    def test_interpreter_matches_the_host_compiler(self, name, tmp_path):
        import textwrap
        source = textwrap.dedent(HOST_PROGRAMS[name])
        want = _normalise(_host_service_run(source, tmp_path / "host"))
        ours = tmp_path / "interp"
        ours.mkdir()
        (ours / "prog.c").write_text(OURS_PRELUDE + source, encoding="utf-8")
        ran = _uasm("run", "prog.c", *HOST_ARGS, cwd=ours,
                         stdin_text=HOST_INPUT, extra_env=HOST_ENV)
        assert _normalise((ran.returncode, ran.stdout)) == want, ran.stderr

    @harness.needs("cc")
    @harness.cases("name", sorted(HOST_PROGRAMS))
    def test_the_c_backend_matches_the_host_compiler(self, name, tmp_path):
        import textwrap
        source = textwrap.dedent(HOST_PROGRAMS[name])
        want = _normalise(_host_service_run(source, tmp_path / "host"))
        ours = tmp_path / "compiled"
        ours.mkdir()
        (ours / "prog.c").write_text(OURS_PRELUDE + source, encoding="utf-8")
        built = _uasm("build", "-b", "c", "-o", "prog.exe", "prog.c",
                           cwd=ours)
        assert built.returncode == 0, built.stderr
        ran = subprocess.run([str(ours / "prog.exe"), *HOST_ARGS],
                             capture_output=True, text=True, input=HOST_INPUT,
                             cwd=str(ours), env=dict(os.environ, **HOST_ENV))
        assert _normalise((ran.returncode, ran.stdout)) == want

    @harness.needs("cc")
    def test_a_backend_without_the_group_refuses_by_name(self, tmp_path):
        """The whole point of the arrangement: the refusal names the
        capability rather than leaving an undefined symbol for the linker."""
        import textwrap
        (tmp_path / "prog.c").write_text(
            OURS_PRELUDE + textwrap.dedent(HOST_PROGRAMS["a_file_round_trip"]),
            encoding="utf-8")
        ran = _uasm("build", "-b", "x86-64", "-o", "prog.out", "prog.c",
                         cwd=tmp_path)
        assert ran.returncode != 0
        assert "'file'" in ran.stdout + ran.stderr

    @harness.needs("cc")
    def test_a_program_that_only_prints_still_runs_anywhere(self, tmp_path):
        """And the other half: adding a filesystem to the library must not
        cost a program that does not use one."""
        (tmp_path / "prog.c").write_text(
            OURS_PRELUDE
            + "#include <stdio.h>\n"
              "#include <stdlib.h>\n"
              "#include <time.h>\n"
              "int main(void){ printf(\"%d\\n\", 7); return 0; }\n",
            encoding="utf-8")
        ran = _uasm("build", "-b", "x86-64", "-o", "prog.out", "prog.c",
                         cwd=tmp_path)
        assert ran.returncode == 0, ran.stdout + ran.stderr


# ── several translation units ───────────────────────────────────────────────
#
# THE ORACLE IS `cc a.c b.c`, which is the thing this is imitating: two files,
# each compiled on its own, joined into one program. What has to be true is
# what a C programmer relies on without thinking about it -- that a `static`
# in one file is not the `static` of the same name in the other, that a
# function defined in one is callable from the other, and that the LIBRARY is
# one library: `errno` set by a failing `fopen` over here is readable over
# there, because there is one `libc.a` and not one per file.

#: name -> (main.c, other.c). Two files is enough to show every rule; three
#: would show the same ones again.
UNIT_PROGRAMS: dict[str, tuple[str, str]] = {
    "statics_do_not_collide": (r"""
        #include <stdio.h>
        static int counter = 100;
        int bump_other(void);
        int other_counter(void);
        int main(void) {
            /* ONE CALL PER STATEMENT: the order in which a call's arguments
               are evaluated is unspecified, and two of these have an effect
               on the third. */
            int bumped = bump_other();
            printf("%d %d %d\n", counter, bumped, other_counter());
            counter += 5;
            printf("%d %d\n", counter, other_counter());
            return 0;
        }
    """, r"""
        static int counter = 7;
        int bump_other(void) { return ++counter; }
        int other_counter(void) { return counter; }
    """),

    "one_library_between_them": (r"""
        #include <stdio.h>
        #include <stdlib.h>
        #include <errno.h>
        int missing(void);
        char *borrow(int n);
        void seed(unsigned s);
        int draw(void);
        int draw_here(void);
        int main(void) {
            char *p;
            printf("%d\n", missing());
            /* `errno` WAS SET IN THE OTHER FILE. One library, so one of it. */
            printf("%d\n", errno == ENOENT);
            p = borrow(16);
            snprintf(p, 16, "%s", "borrowed");
            printf("%s\n", p);
            free(p);
            /* ONE `rand` STATE BETWEEN THE TWO FILES. Not which numbers --
               C does not say what they are and no two libraries agree -- but
               that the sequence CONTINUES across the file boundary: seeded
               twice the same way, the pair (here, here) and the pair (here,
               there) are the same two numbers. */
            {
                int a1, a2, b1, b2;
                seed(12345u); a1 = draw(); a2 = draw();
                seed(12345u); b1 = draw(); b2 = draw_here();
                printf("%d %d\n", a1 == b1, a2 == b2);
            }
            return 0;
        }
    """, r"""
        #include <stdio.h>
        #include <stdlib.h>
        int missing(void) {
            FILE *f = fopen("no-such-file-at-all.txt", "r");
            if (f) { fclose(f); return 0; }
            return 1;
        }
        char *borrow(int n) { return (char *)malloc((size_t)n); }
        void seed(unsigned s) { srand(s); }
        int draw(void) { return rand(); }
        int draw_here(void) { return rand(); }
    """),

    "initialisers_in_both": (r"""
        #include <stdio.h>
        const char *other_msg(void);
        extern int shared;
        static const char *mine = "from the first";
        int main(void) {
            printf("%s / %s / %d\n", mine, other_msg(), shared);
            return 0;
        }
    """, r"""
        static const char *msg = "from the second";
        int shared = 41;
        const char *other_msg(void) { return msg; }
    """),

    "a_tentative_definition": (r"""
        #include <stdio.h>
        void set(void);
        extern int total;
        int main(void) { set(); printf("%d\n", total); return 0; }
    """, r"""
        int total = 12;
        void set(void) { total += 30; }
    """),
}


class TestSeveralTranslationUnits:
    """`uasm build main.c --c:unit other.c`, against `cc main.c other.c`."""

    @harness.needs("cc")
    @harness.cases("name", sorted(UNIT_PROGRAMS))
    def test_the_interpreter_matches_the_host_compiler(self, name, tmp_path):
        import textwrap
        first, second = (textwrap.dedent(t) for t in UNIT_PROGRAMS[name])
        host = tmp_path / "host"
        host.mkdir()
        (host / "main.c").write_text(HOST_PRELUDE + first, encoding="utf-8")
        (host / "other.c").write_text(second, encoding="utf-8")
        built = subprocess.run(
            [HAS_CC, "-std=c11", "-w", "-fcommon", "-o", str(host / "a.out"),
             str(host / "main.c"), str(host / "other.c")],
            capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        want = subprocess.run([str(host / "a.out")], capture_output=True,
                              text=True, cwd=str(host))

        ours = tmp_path / "ours"
        ours.mkdir()
        (ours / "main.c").write_text(OURS_PRELUDE + first, encoding="utf-8")
        (ours / "other.c").write_text(second, encoding="utf-8")
        ran = _uasm("run", "main.c", "--c:unit", "other.c", cwd=ours)
        assert (ran.returncode, ran.stdout) == (want.returncode, want.stdout), \
            ran.stderr

    @harness.needs("cc")
    @harness.cases("name", sorted(UNIT_PROGRAMS))
    def test_the_c_backend_matches_the_host_compiler(self, name, tmp_path):
        import textwrap
        first, second = (textwrap.dedent(t) for t in UNIT_PROGRAMS[name])
        host = tmp_path / "host"
        host.mkdir()
        (host / "main.c").write_text(HOST_PRELUDE + first, encoding="utf-8")
        (host / "other.c").write_text(second, encoding="utf-8")
        built = subprocess.run(
            [HAS_CC, "-std=c11", "-w", "-fcommon", "-o", str(host / "a.out"),
             str(host / "main.c"), str(host / "other.c")],
            capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        want = subprocess.run([str(host / "a.out")], capture_output=True,
                              text=True, cwd=str(host))

        ours = tmp_path / "ours"
        ours.mkdir()
        (ours / "main.c").write_text(OURS_PRELUDE + first, encoding="utf-8")
        (ours / "other.c").write_text(second, encoding="utf-8")
        made = _uasm("build", "-b", "c", "-o", "prog.exe", "main.c",
                     "--c:unit", "other.c", cwd=ours)
        assert made.returncode == 0, made.stdout + made.stderr
        ran = subprocess.run([str(ours / "prog.exe")], capture_output=True,
                             text=True, cwd=str(ours))
        assert (ran.returncode, ran.stdout) == (want.returncode, want.stdout)

    @harness.needs("cc")
    def test_two_definitions_of_one_name_are_refused(self, tmp_path):
        (tmp_path / "main.c").write_text(
            "int helper(void){ return 1; }\nint main(void){ return helper(); }\n",
            encoding="utf-8")
        (tmp_path / "other.c").write_text(
            "int helper(void){ return 2; }\n", encoding="utf-8")
        ran = _uasm("run", "main.c", "--c:unit", "other.c", cwd=tmp_path)
        assert ran.returncode != 0
        out = ran.stdout + ran.stderr
        assert "E1600" in out and "'helper'" in out

    @harness.needs("cc")
    def test_two_mains_are_refused_by_the_name_the_program_used(self, tmp_path):
        """`main` is compiled under another name, and the diagnostic has to
        say the one the programmer wrote."""
        (tmp_path / "main.c").write_text("int main(void){ return 0; }\n",
                                         encoding="utf-8")
        (tmp_path / "other.c").write_text("int main(void){ return 1; }\n",
                                          encoding="utf-8")
        ran = _uasm("run", "main.c", "--c:unit", "other.c", cwd=tmp_path)
        assert ran.returncode != 0
        assert "'main'" in ran.stdout + ran.stderr

    @harness.needs("cc")
    def test_a_unit_that_cannot_be_read_says_so(self, tmp_path):
        (tmp_path / "main.c").write_text("int main(void){ return 0; }\n",
                                         encoding="utf-8")
        ran = _uasm("run", "main.c", "--c:unit", "absent.c", cwd=tmp_path)
        assert ran.returncode != 0
        assert "E1602" in ran.stdout + ran.stderr
