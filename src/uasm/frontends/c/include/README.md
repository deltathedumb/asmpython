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
entropy, an environment, another program — which a backend **declares**. A
program that calls into a group its target has not got is refused at compile
time, by name:

    error: this program needs host services the x86-64 backend does not
    provide: 'file' (for host_file_open)

| what | group | reached by |
| --- | --- | --- |
| `fopen`, `fclose`, `fseek`, `remove`, `tmpfile` | `file` | opening a file |
| `fgetc`, `fgets`, `fread`, `scanf` on any stream | `file` | reading anything |
| `time`, `clock`, `timespec_get` | `time` | asking what time it is |
| `getenv` | `env` | asking about the environment |
| `main(argc, argv)` | `env` | declaring parameters |
| `system` | `proc` | running another program |

`lower._prune` drops the declaration of a host service nothing reaches, which
is what makes the second column true rather than aspirational: `printf`
reaches the write path through a function pointer that only `fopen` ever sets,
so hello world names no group at all.

What is still absent says so rather than approximating:

| absent | because |
| --- | --- |
| `rename` | the `file` group has ten operations and no rename; copy-and-remove is not one, and `bundled/os.py` refuses `os.rename` for the same reason |
| `<threads.h>` | there is no way to create one |
| `_Imaginary` | Annex G, which an implementation may leave out, as gcc does |

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

The one header that cannot exist at all is PRESENT AND REFUSES, with
`#error` and a sentence saying what to write instead: a missing file is a
mystery and a refusal is an answer.

`<stdatomic.h>` is supported and `<threads.h>` is not, which is not a
contradiction. With one thread a plain load is indivisible with respect to
every other operation in the program, which is the whole of what
`atomic_load` promises; the memory orders are accepted and ignored because
there is no second observer for them to order anything against.

All thirty-one headers C23 requires are here, and a test includes every one
of them alone and then all of them together -- a macro one defines can break
the next, and one translation unit is the only place that shows.

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
