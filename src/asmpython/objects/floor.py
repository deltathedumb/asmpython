"""The platform floor: everything a backend must supply that is not IR.

STAGE 2 OF `docs/INERT-RUNTIME.md`. A backend that wants dynamic Python has to
define 229 `apy_*` functions today, which is why exactly one backend does. The
plan is to write that runtime in IR instead -- and a runtime written in IR still
cannot talk to the machine, because the IR has no I/O and no way to ask for
memory. THIS FILE IS WHAT IT ASKS THROUGH.

    plat_write(fd, buf, n) -> i64      bytes written, or -1
    plat_exit(code)                    does not return
    plat_heap(n) -> ptr                n more bytes, or null

**Three functions per backend instead of 229.** That number is the deliverable
the design document names, and this is it written down.

WHY THESE THREE AND NOT MORE. Measured from the C runtime: 26 distinct libc
functions, of which `strcmp`, `strlen`, `memcpy`, `memcmp` and `memset` are
loops over memory the IR expresses directly, `malloc`/`free` are an allocator
that can be written over ONE arena, and `snprintf`/`strtod` for floats already
have a libc-free implementation in `link/baremetal.py` -- written because a
bare-metal target has no libc either. What is left is a way to emit bytes, a
way to stop, and a way to get memory. Nothing above those is platform; it is
just code that has not been written in the subset yet.

WHY NOT A RICHER FLOOR. The five `put_*` functions this sits beside are the
counter-example, and they are the reason to be strict here. `put_bool` knows
that Python spells a true value `True`; `put_float` knows Python's float repr
is the shortest decimal that reads back. Those are LANGUAGE facts, so every
backend that implements them owes the language, and the floor stops being
three functions the moment one of them knows what a bool is. Nothing here
knows what a Python value is. `plat_write` takes bytes.

## The contracts, exactly

`plat_write(fd, buf, n)` writes `n` bytes from `buf` to descriptor `fd` -- 1 is
standard output and 2 is standard error, and no other value need be supported.
Returns the number of bytes written, which may be short, or -1. It is not
buffered on the caller's behalf: a runtime that wants buffering builds it in
the subset, where every backend gets the same one.

`plat_exit(code)` ends the process with `code` as its status and DOES NOT
RETURN. A backend that cannot literally not-return (a class file's `main` must
fall off its end) is still obliged not to run the caller's next instruction.

`plat_heap(n)` returns the address of a fresh region of at least `n` bytes, or
null if it cannot. The region is never freed and REGIONS ARE NOT GUARANTEED
CONTIGUOUS -- an allocator written over this must chain them rather than assume
one growing block, because `sbrk` gives contiguity and `malloc` does not, and
requiring it would rule out the simplest implementation on the platform that
has the most memory to give.

## What defines them

`C_SOURCE` here, for every backend that goes through a C toolchain; the
bytecode in `backends/jvm/runtime.py`; and `Interpreter._host`, so the IR
interpreter runs the same runtime the backends run -- which is the point of
the whole exercise and is checked by the corpus comparing all three.
"""
from __future__ import annotations

#: The floor, as IR signatures: name -> (argument types, return type).
#:
#: Read by the Python frontend to declare and call them, by the JVM backend to
#: know which externals it can satisfy, and by the tests that assert no fourth
#: symbol quietly appeared. Written here rather than parsed out of the C
#: because there are three of them and a backend author needs the list before
#: there is any C in sight.
FLOOR: dict[str, tuple[tuple[str, ...], str]] = {
    "plat_write": (("i64", "ptr", "i64"), "i64"),
    "plat_exit": (("i64",), "void"),
    "plat_heap": (("i64",), "ptr"),
}

NAMES = tuple(FLOOR)

#: The C implementation. `@STATIC@` and `@PTR@` are substituted exactly as
#: `objects/support.py` substitutes them -- the C backend inlines everything
#: `static` and passes pointers as `uintptr_t`, and a second convention here
#: would be a second thing to keep in step.
C_SOURCE = r"""/* THE PLATFORM FLOOR. See objects/floor.py for the contracts. */

@STATIC@int64_t plat_write(int64_t fd, @PTR@ buf, int64_t n)
{
    /* UNBUFFERED, by contract. A runtime that wants buffering builds it above
       this, where every backend gets the same one -- and where the flush
       points are visible in the IR rather than being a property of whichever
       libc the toolchain found. `fflush` is what makes that true here: stdout
       to a pipe is block-buffered by default, so without it the interleaving
       of stdout and stderr depends on where the output is going. */
    FILE *s = (fd == 2) ? stderr : stdout;
    size_t w = fwrite((const void *)buf, 1, (size_t)n, s);
    if (fflush(s) != 0) return -1;
    return (int64_t)w;
}

@STATIC@void plat_exit(int64_t code)
{
    exit((int)code);
}

@STATIC@@PTR@ plat_heap(int64_t n)
{
    /* `malloc`, and the contract is written so that it can be. A caller must
       chain regions rather than assume one growing block, precisely so this
       does not have to be `sbrk` -- which is not portable, is deprecated on
       macOS, and does not exist on Windows at all. */
    if (n <= 0) return (@PTR@)0;
    return (@PTR@)malloc((size_t)n);
}
"""


def c_source(*, static: bool = False, ptr: str = "void *") -> str:
    """`C_SOURCE` with its substitutions made.

    The same two knobs `objects.support.host_functions` takes, for the same two
    consumers: a linked translation unit, and the C backend's self-contained
    output.
    """
    return (C_SOURCE.replace("@PTR@", ptr)
            .replace("@STATIC@", "static " if static else ""))
