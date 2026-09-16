# The C standard library, in C

Every header here is compiled by the frontend that ships it. There is no
prebuilt library, no separate object file and nothing written in Python that
pretends to be C: `#include <stdio.h>` reads `stdio.h` from this directory and
the result goes through the same lexer, preprocessor, parser and lowering as
the program that included it.

## What is underneath

Three functions, and `objects/floor.py` argues at length for why it is three:

    plat_write(fd, buf, n) -> i64      bytes written, or -1
    plat_exit(code)                    does not return
    plat_heap(n) -> ptr                n more bytes, or null

Nothing in this directory calls anything else. That is what makes a C program
built here run on **every** backend and in the IR interpreter: a backend that
can already run a Python program has already implemented the whole of what a C
one needs.

It is also why some things are missing rather than approximated:

| absent | because |
| --- | --- |
| `scanf`, `fgets`, `fread`, `fopen` | the floor writes; it cannot read |
| `time`, `clock` | nothing can ask the host what time it is |
| `getenv`, `system` | there is no environment and no command processor |
| `<setjmp.h>` | `longjmp` restores a machine frame; the IR has no frames |

Each of those returns the value the standard defines for "unavailable" --
`NULL`, `EOF`, `(time_t)-1` -- rather than something plausible a program would
go on to do arithmetic with.

## Why the definitions are `static`

A translation unit is the whole program here; there is no separate
compilation, so a header may carry definitions and not only declarations.
`static` is what makes those definitions this unit's own: the IR symbol gets a
`c.` prefix (see `parser._merge`) so that the C backend, whose output is
self-contained and includes the real `<stdio.h>`, does not end up with two
`printf`s of different signatures.

Unreachable ones are dropped before the module leaves the frontend
(`lower._prune`), because a linker would drop them and this frontend emits a
module rather than an object file.

## The two that are worth reading

`stdio.h` converts a double to decimal **exactly**, with a base-10^9 bignum:
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
