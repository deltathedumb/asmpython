# The C standard library, in C

Every header here is compiled by the frontend that ships it. There is no
prebuilt library, no separate object file and nothing written in Python that
pretends to be C: `#include <stdio.h>` reads `stdio.h` from this directory and
the result goes through the same lexer, preprocessor, parser and lowering as
the program that included it.

## What is underneath

Two layers, and a program pays for the second only if it asks for it.

The first is the platform floor, and `objects/floor.py` argues at length for
why it is three functions:

    plat_write(fd, buf, n) -> i64      bytes written, or -1
    plat_exit(code)                    does not return
    plat_heap(n) -> ptr                n more bytes, or null

`printf`, `puts`, `malloc` and everything built on them reach that and nothing
else. A program that only computes and prints therefore runs on **every**
backend and in the IR interpreter, with no second runtime to implement.

The second is `objects/hostsvc.py`'s optional groups — a filesystem, a clock,
entropy, an environment, another program, threads — which a backend
**declares**. A
program that calls into a group its target has not got is refused at compile
time, by name:

    error: this program needs host services the x86-64 backend does not
    provide: 'file' (for host_file_open)

| what | group | reached by |
| --- | --- | --- |
| `fopen`, `fclose`, `fseek`, `remove`, `tmpfile` | `file` | opening a file |
| `fgetc`, `fgets`, `fread`, `scanf` on any stream | `file` | reading anything |
| `time`, `clock`, `timespec_get` | `time` | asking what time it is |
| `<threads.h>`, `_Thread_local` | `thread` | starting one, or one copy per thread |
| `getenv` | `env` | asking about the environment |
| `main(argc, argv)` | `env` | declaring parameters |
| `system` | `proc` | running another program |

`lower.prune` drops the declaration of a host service nothing reaches, which
is what makes the second column true rather than aspirational: `printf`
reaches the write path through a function pointer that only `fopen` ever sets,
so hello world names no group at all.

What is still absent says so rather than approximating:

| absent | because |
| --- | --- |
| `rename` | the `file` group has ten operations and no rename; copy-and-remove is not one, and `bundled/os.py` refuses `os.rename` for the same reason |
| `*_SNAN` | a signaling NaN signals by raising the invalid-operation exception, and the IR has no instruction that reads a floating-point status flag; C defines those macros only where the type has one |
| `_Imaginary` | Annex G, which an implementation may leave out, as gcc does |

`long double` is 80-bit extended, in software: `support.py`'s `ldouble` unit
is the format written out in C, and the differential suite checks it against
x87 hardware.

The `l` **series** in `<math.h>` — `sinl`, `expl`, `powl` and the rest —
compute in double and widen, so they answer to about a double's precision in
the wider type. Everything that is exact **by definition** is exact, and is
written on the sixteen bytes rather than through a double: `sqrtl`, the four
operators, the conversions, `strtold` and `%Lf`, `frexpl`, `ilogbl`, `logbl`,
`ldexpl`, `modfl`, `truncl` and the other four roundings, `fmodl`,
`remainderl`, `remquol`, `nextafterl`, `fabsl`, `copysignl`, `fmaxl`,
`fminl`. `floorl(1e30L)` computed in double would be a *different integer*
rather than a less precise one, which is the difference between the two
lists. `fmal` is the one that is not fused, and says so where it is written.

`<tgmath.h>` dispatches to all of them: a `long double` argument picks the
`l` function, which is what makes the paragraph above visible to a program
that only ever writes `sqrt`.

The multibyte encoding is UTF-8, always: `mbrtowc` decodes it, `MB_CUR_MAX`
is 4, and `mbstowcs` of two UTF-8 bytes is one wide character. C leaves the
execution character set to the implementation and this one chose the
source's; glibc's `"C"` locale chose one byte per character and answers −1
for the same input, which is a different choice rather than a better one.
`<locale.h>` has one locale, so there is nowhere to put the other answer.

`localtime` is `gmtime`. The host services can say what time it is and cannot
say what the local offset from UTC is — there is no `TZ` that would mean
anything on a target without an environment — so the calendar is UTC and
`tm_isdst` is 0 rather than -1: it is not unknown, it is not in effect.

`<setjmp.h>` works, and not by saving a frame: `longjmp` sets a flag, every
call site in the program checks it as its call returns, and the frame that
recognises the jump's token branches back to its own `setjmp`. It costs a
load and a branch per call in a program that uses it, and nothing in one that
does not. `longjmp.py` says why it is shaped that way.

`<complex.h>` works. A complex value is two floats side by side, which is
what C says it is and what an aggregate already is here, so the machinery
that passes and returns a struct passes and returns one; `*` and `/` are
Annex G's algorithms rather than the four-multiply formula, because what an
infinity times a zero must produce is the hard part and libgcc does the same
thing. `<tgmath.h>` dispatches to it.

`<threads.h>` works where the target has threads: `thrd_create`, a mutex, a
condition variable, `call_once` and `tss_t` over the `thread` group, and
`_Thread_local` compiled onto the same keys. The C backend has them over
pthreads and so does the reference interpreter, whose stack pointer is per
thread for exactly this reason. `thrd_create` can still FAIL -- on Windows,
and on any target without the group -- which C allows and a portable program
checks.

`<stdatomic.h>` is the one to read carefully now that there can be two
threads: its operations are ordinary loads and stores with the right names,
which was right for one thread and is not a promise this library can keep
for two. A program with real sharing wants a mutex.

All thirty-one headers C23 requires are here, and a test includes every one
of them alone and then all of them together -- a macro one defines can break
the next, and one translation unit is the only place that shows.

## The three implementation headers

`__uasm_base.h` is the platform floor and `__uasm_host.h` the optional
groups; both are there so that several headers can name the same externs.
The other two exist to break a circle rather than to share anything:
`<string.h>` needs the allocator for `strdup`, the allocator needs `memcpy`
and `memset`, and `<stdlib.h>` — where the allocator lives as far as a
program is concerned — already includes `<string.h>`. So `__uasm_mem.h` has
the three that move bytes and `__uasm_alloc.h` has the arena, and a program
that includes `<string.h>` or `<stdlib.h>` sees exactly what C says it
should.

`<stdlib.h>` includes `<stdio.h>`, which is more than C asks for and is what
`strfromd` costs: C23 puts it in `<stdlib.h>` and it is `snprintf` with one
conversion in it. Nothing is paid for at run time — `lower.prune` drops
every definition the program does not reach — and `<stdio.h>` does not
include `<stdlib.h>`, so the circle does not close.

## Why the definitions are `static`

A header may carry definitions here, not only declarations, because there is
no separate `libc` to link: `#include <stdio.h>` compiles `printf`. `static`
is what makes those definitions this unit's own, and the IR symbol gets a
`c.` prefix (see `parser._merge`) so that the C backend, whose output is
self-contained and includes the real `<stdio.h>`, does not end up with two
`printf`s of different signatures.

`c.` AND NOT THE UNIT'S OWN PREFIX, which is the one thing that is not
obvious. A build with several translation units gives each one a prefix of
its own — `c0.`, `c1.` — so that two files may each have a `static int
count`. The bundled headers share `c.` across the whole build instead, and
`merge.py` keeps one copy: otherwise every file would get its own `errno`,
its own `malloc` arena and its own `rand` state, and a `fopen` that failed in
one file could not be diagnosed in another. A real toolchain does not have
that problem because libc is one archive linked once; this is the same
answer.

Unreachable definitions are dropped before the module leaves the frontend
(`lower.prune`), because a linker would drop them and this frontend emits a
module rather than an object file.

## The two that are worth reading

`stdio.h` converts a double to decimal **exactly**, with a base-10^9 bignum,
and `__uasm_num.h` converts it back the same way — 54 rounds of
compare-and-subtract against a big integer, which is a whole long division for
a double and needs no bignum divide. That is what makes `strtod(buf)` recover
the value `printf("%.17g", v)` wrote, for every `v` including the subnormals:
every finite double is a terminating decimal, so `printf("%f", 1e300)` has a
right answer with 301 digits in it and the usual trick of scaling into [1,10)
gets seventeen of them and then lies. Rounding is ties-to-even, which is what
the FPU's default mode gives a hosted libc.

`math.h` is algorithms rather than calls, because there is no libm below it.
`sqrt` is Newton from a bit-twiddled guess; `exp` and `log` reduce and sum a
series; `sin` and `cos` reduce modulo pi/2 with a three-part constant so a
large argument keeps its significant bits. It is accurate to within a unit in
the last place, which is the difference between a libm somebody spent a career
on and one that fits in a header.
