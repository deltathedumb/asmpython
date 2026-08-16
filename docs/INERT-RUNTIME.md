# The inert runtime

**Goal: a backend stops having to define the object runtime.**

Today a backend that wants dynamic Python must supply 229 `apy_*` functions.
Exactly one does. `backends/jvm/emit.py:49` says so in its own words:

> WHAT IS NOT HERE. The Python frontend's DYNAMIC path calls a runtime of 258
> `apy_*` functions -- the object model: lists, dicts, strings, classes,
> generators, exceptions -- which exists only as C. Nothing in a class file can
> call it, and it is reported as `BackendUnsupported` naming the symbol [...]
> The statically typed subset needs none of it.

So the JVM backend compiles annotated Python and refuses the language. Done is:
`--backend jvm` runs the conformance suite.

## What exists now, and how many times

| | lines | language | consumed by |
| --- | --- | --- | --- |
| `link/objects.py` | 15,560 | C (as one string, `OBJECTS_C`) | the C backend, and the machine backends via a C toolchain |
| `link/unicode_table.py` | 909 | C, generated | spliced into the above at `/* @UNICODE_TABLE@ */` |
| `ir/objects_host.py` | 8,583 | Python | the IR interpreter |
| a JVM equivalent | 0 | — | nothing; `BackendUnsupported` |

**The runtime is written twice and needed a third time.** That is not an
accident of history, it is what the current design requires: `link/objects.py`
`signatures()` reads every `APY_API` symbol out of the C and types it as
`ptr`/`i64`/`f64`, so the IR calls the runtime as OPAQUE EXTERNAL SYMBOLS. An
opaque symbol is something each backend must go and find.

And the two copies drift. TODO.md records the classes: identity and interning
(`xs[1] is xs` true in one path, false in the other), container `repr`, a frame
slot holding a handle in the C and an object on the host, `_LIVE_AGENS` and the
position table and the task registry each leaking across runs on the host and
not in the C. Every one was found by the corpus rather than by conformance,
because conformance runs one path.

## The premise, and it is proven

The IR is already LLVM-lite: `alloca`, `load`, `store`, `offset`, `bitcast`,
`func_addr`, `call_ptr`, `switch`, full arithmetic on `i8..u64`/`f32`/`f64`, and
`ptr`. `ir/types.py` says the intent outright -- "A struct is a `ptr` to storage
you obtained from `alloca`, and its fields are byte offsets you compute."

Measured, not assumed. A hand-built function that allocates 16 bytes, stores a
kind and a payload, and reads both back:

    VERIFIER        accepted
    INTERPRETER     1241 (correct)
    JVM BACKEND     emitted Probe.class, 822 bytes
    C BACKEND       emitted out.c

**The JVM backend already compiles raw-memory IR.** It models memory as one
`byte[]` with a pointer as an index into it, and `call_ptr` through a generated
dispatcher (`backends/jvm/emit.py`, `_number_functions`). Nothing about the
runtime's ALGORITHMS is beyond it. What defeats it is only that those
algorithms are C.

So the runtime does not need a new representation. It needs to BE IR.

## How to get there, and why not the other ways

### Rejected: write a C frontend

Reuses the 15,560 verified lines, and asmpython already builds frontends
(`frontends/python`, `frontends/x86`). Rejected on scope: the C in `objects.py`
uses 22 typedefs, 18 unions, 19 enums, 18 function-pointer types, 63 varargs,
364 designated initialisers, 271 casts and 332 ternaries. Those are the hard
parts of C, not the easy ones. A frontend covering them is a multi-thousand-line
compiler whose bugs would be indistinguishable from runtime bugs.

### Rejected: lift the compiled object code

`frontends/x86` exists and lifts x86-64 to IR, so `gcc objects.c` then lift is a
path that needs no new frontend. Rejected because the result carries x86
semantics and the C ABI into every backend, keeps libc as external symbols, and
produces IR nobody can read -- so a runtime bug would be debugged in lifted
assembly.

### Rejected: a generator emitting both C and host Python

Removes the drift between the two existing copies and gives the JVM nothing.
It solves the smaller half of the problem.

### Chosen: extend the static path into a systems subset, and write the runtime in it

The Python frontend already has two paths, and the static one **emits no
`apy_*` at all** -- it compiles annotated `int`/`float`/`bool`/`None` to machine
words with no allocation. That is a systems language with the object model
removed, and it is missing exactly one thing: raw memory.

**This is what makes the bootstrap non-circular.** A runtime written in the
DYNAMIC subset would need the object runtime to run its own source -- the
circularity that kills the obvious version of "write it in Python". A runtime
written in the STATIC subset needs nothing but the machine, so there is no
floor to stand on that is not already there.

What has to be added to the static path:

* the machine types as annotations -- `i8 i16 i32 i64 u8 u16 u32 u64 f32 f64
  ptr`, which the IR already has and `analysis.BY_NAME` does not expose
* memory intrinsics lowering straight to IR ops: `load`, `store`, `offset`,
  `alloca`, and `sizeof`-style constants
* `func_addr`/`call_ptr` for the dispatch tables the runtime uses (18
  function-pointer types in the C today)
* module-level mutable storage -- `global_addr` exists in the IR; the static
  path does not surface it

None of it is new IR. All of it is surfacing IR the backends already lower.

### And it deletes `objects_host.py`

This is the argument that decides it. If the runtime is IR, the IR interpreter
runs THE SAME RUNTIME the C backend runs. `ir/objects_host.py`'s 8,583 lines
stop existing, and every drift class listed above becomes structurally
impossible rather than something a corpus has to keep catching.

## The platform floor

The runtime cannot be pure IR: something must talk to the machine. Measured
from the C -- 26 distinct libc functions, by call count:

    strcmp 208  snprintf 60  malloc 58  free 51  memcpy 48  exit 26
    fputs 25  strlen 22  floor 18  realloc 10  pow 10  memcmp 10
    round 8  fabs 7  calloc 6  strtod 5  isnan 5  fmod 5  sqrt 4
    fwrite 4  memset 3  isinf 3  ceil 2  strtoll 1  sprintf 1  fprintf 1

Most of that is not platform at all. `strcmp`, `strlen`, `memcpy`, `memcmp`,
`memset` are loops over memory the IR expresses directly. `malloc`/`free` can be
an allocator written IN the subset over one arena -- which is what the JVM
backend's `byte[]` already is. `snprintf`/`strtod` for floats are the awkward
ones, and `link/baremetal.py` ALREADY HAS a libc-free implementation of both
(it prints to N significant digits and parses back until it round-trips,
because a bare-metal target has no libc either).

So the irreducible floor is roughly:

    write(fd, ptr, len)     stdout and stderr
    exit(code)
    arena base and size     or sbrk, if the backend prefers

**Three functions per backend instead of 229.** That number is the deliverable.

## Staged, with what proves each stage

Every stage keeps the tree working. The runtime in C stays the one that ships
until a stage replaces a piece of it and the corpus agrees.

1. **The subset.** Machine types and memory intrinsics in the static path.
   PROVED BY: a unit test compiling a function that allocates, stores a tagged
   cell and reads it back, checked through the IR verifier and the IR
   interpreter. Cheap -- no backend, no gcc.
2. **The floor.** Define the platform interface and implement it for the C
   backend (trivially, over libc) and the JVM backend (over `byte[]` and
   `System.out`). PROVED BY: a program that prints, on both backends.
3. **One real kind, end to end.** Port the smallest complete object kind -- the
   integer cell and its arithmetic -- to the subset. Keep the C version and run
   BOTH. PROVED BY: the multi-path corpus, which already compares CPython, the
   IR interpreter and the C backend on 143 programs.
4. **The allocator**, in the subset, over the floor's arena. PROVED BY: the
   same corpus, plus a stress program that exercises reuse.
5. **Kind by kind**, largest surface first: str, list, dict, then classes,
   exceptions, generators. Each lands with the C version still present and a
   switch choosing which is compiled in, so a regression is one flag away from
   being isolated.
6. **Delete the C, delete `objects_host.py`.** PROVED BY: 1668/1668 with
   `--backend c`, and the same suite with `--backend jvm`, which is the goal
   stated at the top.

## Constraints carried in

* `conformance/cases/` is never edited. It is the oracle.
* `archived/legacy/` is not touched. It is not part of 3.14.
* `python -m tests.harness`, not pytest.
* Never two heavy measurements at once -- they produce failures that are not in
  the code. See TODO.md, "Concurrent runs make the suite lie".
* A conformance measurement is a CHECKPOINT, not a check. Work in the cheap
  loops: the IR interpreter needs no C compiler at all (~2s), a frontend-only
  `compile_source(...).ok` is a tenth of a second, and the corpus is ~2.5
  minutes for three execution paths.
* What must not regress: **1668/1668** on spec+cpython, the 429-case multi-path
  corpus, and the unit and integration suites.

## Traps this work will hit

Written down because each one has already cost time here.

* **A suspension may appear wherever an expression may**, and nothing computed
  before it may live in a register. Not directly this work's problem, but the
  runtime's generator and coroutine machinery is the thing that rule exists for.
* **Anything a compiled program keeps in a FILE STATIC belongs on the host** --
  broken five times. In the ported world there is one runtime and this trap
  changes shape rather than disappearing: module-level storage in the subset is
  per-PROGRAM in a binary and per-PROCESS in the interpreter.
* **Two runtimes must agree about identity.** Interning and handle identity
  produced a whole class of divergence. One runtime removes the class.
* **A path that answers for kinds it was never told about is worse than one
  that refuses.** `apy_eq_raw` fell through to a numeric comparison that read a
  pointer for non-numbers, and it was invisible because the backend emits one
  static buffer for two identical literals.
* **Estimate the hook, not the surface.** bytes methods looked like 52 changes
  and were one, because dispatch chooses the symbol in a single place.
