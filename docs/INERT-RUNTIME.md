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

1. **The subset.** ***DONE*** -- see "What stage 1 landed" below. Machine types
   and memory intrinsics in the static path.
   PROVED BY: a unit test compiling a function that allocates, stores a tagged
   cell and reads it back, checked through the IR verifier and the IR
   interpreter. Cheap -- no backend, no gcc.
   `tests/asmpython/unit/test_machine_subset.py`, 31 tests, ~1 second.
2. **The floor.** ***DONE*** -- see "What stage 2 landed" below. Define the
   platform interface and implement it for the C backend (trivially, over libc)
   and the JVM backend (over `byte[]` and `System.out`).
   PROVED BY: a program that prints, on both backends.
   `tests/asmpython/integration/test_platform_floor.py`.
3. **One real kind, end to end.** ***DONE for construction; arithmetic
   deferred*** -- see "What stage 3 landed". Port the smallest complete object
   kind -- the integer cell and its arithmetic -- to the subset. Keep the C
   version and run BOTH. PROVED BY: the multi-path corpus, which already
   compares CPython, the IR interpreter and the C backend on 143 programs.
   `tests/asmpython/integration/test_ported_int.py`, 13 tests.
4. **The allocator**, in the subset, over the floor's arena. PROVED BY: the
   same corpus, plus a stress program that exercises reuse.
5. **Kind by kind**, largest surface first: str, list, dict, then classes,
   exceptions, generators. Each lands with the C version still present and a
   switch choosing which is compiled in, so a regression is one flag away from
   being isolated.
6. **Delete the C, delete `objects_host.py`.** PROVED BY: 1668/1668 with
   `--backend c`, and the same suite with `--backend jvm`, which is the goal
   stated at the top.

## What stage 1 landed

The systems subset exists. A function annotated with a machine width takes the
static path, so **it emits no `apy_*` at all** -- which is the property the
whole plan rests on and is asserted directly against the IR rather than
inferred from the program compiling.

### The vocabulary

    i8 i16 i32 i64  u8 u16 u32 u64  f32 f64  ptr      types
    i32(x)  u64(x)  f64(x)  ptr(x)                    conversions
    alloca(n)                                         frame storage -> ptr
    load(T, addr)                                     -> T
    store(T, value, addr)
    offset(addr, bytes)                               -> ptr
    sizeof(T)                                         a constant

Every name is spelled as the IR spells it, so a diagnostic, the IR text and the
backend all say `u32` and mean the one thing. `bool` is already `i1` and is not
respelled: one name per storage class, and `bool` is the one Python has.

**Nothing is new grammar.** `load(i64, p)` rather than `*p`, `offset` rather
than `p + 1`, `sizeof(i64)` rather than a keyword -- so the subset stays
parseable by `ast` and readable by every tool that already reads Python, and
the grammar this frontend accepts did not grow by one node. The type argument
comes FIRST, as LLVM writes the same instruction, so the width being read is
the first thing on the line rather than something you infer from where the
value lands.

**Nothing is reserved.** The intrinsics and the width names are looked up after
the module's own functions, exactly as `int` is, so a program with its own
`def load(...)` keeps it. Sixteen new names cost nothing.

### The one rule, and why it is the one

**Widths do not convert themselves.** Python's tower widens `bool` to `int` to
`float` because none of those loses anything; `i64` to `i32` loses half the
value. A width exists *because something else reads the same bytes*, so an
implicit conversion is a silent disagreement about a struct layout -- the worst
bug this subset can have, and the one it would exist to cause.

A LITERAL is the exception and adapts to the type beside it, because `n + 1`
should not have to be written `n + u32(1)`: a literal has no width of its own to
lose. One that does not fit is refused rather than wrapped -- `x: u8 = 300`
quietly becoming 44 is exactly what makes a width annotation worse than none.
`sizeof(i64)` adapts the same way, and arithmetic over it folds, so a layout can
be written `alloca(sizeof(i64) + sizeof(ptr) + sizeof(i64) * 4)` instead of as
`48` with a comment saying where 48 came from.

Ten new diagnostics enforce it (`E0012`-`E0019`, `E0033`, `E0034`), each with
its own code because each has its own fix. The refusals get as much test room
as the acceptances: a rejected program is not the failure mode that matters,
a program that silently truncates and returns a plausible answer is.

### What it caught on the way in

Two bugs that were live in the existing frontend, both invisible to every test
that existed because nothing had ever asked for a width:

* **A comparison was made at `i64` whatever its operands were**, so a `u64`
  above 2^63 sorted below zero -- and the coercion that got it there was a
  same-width truncation the verifier had no reason to object to.
* **`print` chose its writer on the Python type**, so a machine `f64` went
  through `put_int`. That does not print a wrong float; it prints an integer,
  because the coercion truncates first.

Both are the shape the traps section below warns about: an answer that is
plausible rather than absent.

### What is deferred, and to where

`func_addr`/`call_ptr` for dispatch tables and `global_addr` for module-level
storage are named in "what has to be added to the static path" above and are
NOT in stage 1. They are not needed to lay out and read an object, which is
what stage 1 had to prove; they are needed to dispatch on one, which is stage
3's first requirement. Nor is `alloca` with a computed size -- frame storage is
laid out before the function runs, and a runtime size is the allocator's job,
which is stage 4.

## What stage 2 landed

The floor is three functions, and the number is now enforced by a test rather
than asserted by this document.

    plat_write(fd, buf, n) -> i64      bytes written, or -1
    plat_exit(code)                    does not return
    plat_heap(n) -> ptr                n more bytes, or null

`link/platform.py` holds the contracts and the C implementation; the JVM's is
bytecode in `backends/jvm/runtime.py` over the `byte[]` it already had; the IR
interpreter's is in `Interpreter._host`. **One list, three implementations** --
a fourth function cannot be satisfied by a backend without being declared in
the contract, which is checked.

They are callable from the subset by name, as ordinary external calls. Not
intrinsics: each is `Op.CALL` of a symbol, which is exactly what makes them the
one thing a backend still has to supply. And a program that does not ask does
not pay -- the declarations are dropped when nothing calls them, so a program
that only does arithmetic acquires no dependency on the platform at all.

### Why the floor is this and not the five functions next to it

The frontend already required five: `put_int`, `put_float`, `put_bool`,
`put_none`, `putchar`. They are the counter-example that sets the rule.
`put_bool` knows that Python spells a true value `True`; `put_float` knows
Python's float repr is the shortest decimal that reads back. Those are LANGUAGE
facts, so every backend that implements them owes the language -- and a floor
stops being three functions the moment one of them knows what a bool is.
Nothing in this floor knows what a Python value is. `plat_write` takes bytes.

### The proof, and why it is that program

A program that formats a signed 64-bit integer to decimal and writes the bytes,
**with no `put_int` anywhere in it**, producing identical output under the IR
interpreter, the C backend and the JVM backend.

That is the smallest thing that is undeniably runtime work rather than a smoke
test: it needs memory it can index, arithmetic at a fixed width, a loop whose
trip count depends on the value, and a way to emit bytes. And `put_int` is
precisely what every backend has to implement today, so writing it once in the
subset is the shape every later stage takes.

`plat_exit` is tested for the half of its contract a status check does not
reach: the program writes, exits 7, and writes again -- and the second write
must not happen on any of the three paths.

### What it cost to find out

* **`//` and `%` floor even at a machine width**, because the subset is Python.
  `-1234 // 10` is -124 and `-1 // 10` is -1, so a digit loop written on the
  negative side never terminates -- it ran the index off the front of the
  buffer and faulted. The fix is to work in `u64`, where both operands are
  non-negative, the floor correction is dead code, and `0 - m` gives the
  magnitude of -9223372036854775808 too. This is the subset being RIGHT and
  the program being wrong, and it is the first evidence that writing a runtime
  in Python-with-widths is different from writing it in C.
* **One stack map covers every label in a JVM method**, so a local it names has
  to be live at every branch TARGET. Choosing the output stream after the
  bounds checks verified as "top is not assignable to PrintStream", which names
  the slot rather than the jump that skipped it.
* **`asmpython run` reported 0 whatever the program did.** `plat_exit(7)` now
  ends it with 7, as every compiled path already did. NOT FIXED, and worth
  knowing: the entry's RETURN value also becomes a compiled program's exit
  status and still does not become the interpreter's. That divergence predates
  this work.

### What the JVM backend can now do that it could not

`backends/jvm/emit.py` says in its own words that it cannot compile dynamic
Python because the object runtime is C and nothing in a class file can call it.
It still cannot. But it now runs a program that does real runtime work --
formats a number, asks for heap, writes bytes -- with nothing outside the IR
except three methods it has. That is the entire bet of this document, executing.

## What stage 3 landed

**The object runtime is no longer entirely C.** `apy_from_int` and `apy_as_int`
are `src/asmpython/runtime/int_cell.py`, compiled by asmpython's own frontend
and spliced into every program that builds an object. The C definitions they
replace become declarations, so the runtime's hundred-odd callers still have
one in scope and nothing is defined twice.

An int is the right first kind: a tag and a payload, one cache, no ownership
and no variable-length part -- and the most-called constructor there is, so a
wrong cell is not a subtle failure. `a = 1; b = 1; a is b` is still True and
257 is still False; the cache is a `reserve("apy_small_ir", 2096)` sharing
exactly what CPython shares, boundary included.

### The C runtime is still a supported arrangement

This is the point, not a concession. The reason to write the runtime in IR is
that a backend should not HAVE to define 229 functions -- which argues for
making the C unnecessary, not for making it unavailable.

    --object-runtime c        the whole build; nothing spliced
    Backend.object_runtime    one function, for a backend with its own

They cannot get out of step, because the C omits **exactly what the module
defines**: no splice is no omission, automatically. Both are tested rather than
left as a flag nobody exercises, because the ported set grows every stage.

### Three deferrals from stage 1 came due

`reserve(name, bytes)` for named static storage, zeroed, for the whole run --
the fourth way to get an address, and the one a cache needs. Calls into the
still-C runtime, typed from `signatures()`, so a half-ported runtime can call
across the line. And a LIBRARY compile mode: definitions, no entry, everything
exported.

`func_addr`/`call_ptr` are still deferred. Constructing an object does not need
them; dispatching on one does, and that is stage 5's first requirement.

### The finding that matters most is negative

The splice was going to fix the IR interpreter for free -- define the function,
and it runs the same IR the C backend compiles. **It cannot.**
`ir/objects_host.py` represents an `apy_value` as a HANDLE into a Python-side
table; the ported code represents one as an ADDRESS. A ported `apy_from_int`
hands back something every unported function rejects:

    trap: apy_is: 2248 is not a runtime value handle

So the port is **all-or-nothing on the interpreter path**, and stage 6 changes
shape: `objects_host.py` cannot be retired function by function, only in one
step, once every kind it claims is ported. `Interpreter._call` states the rule
and the reason. The compensation is real: the interpreter stays an ORACLE, so
the corpus compares ported against unported on every program it runs, which is
a sharper test than having both paths run the same code.

### What it cost

* **A docstring was `unsupported expression: Constant` on the static path.**
  Lowering skipped bare constants; analysis did not. Pre-existing -- the
  runtime source is the first static-path code anyone tried to document.
* **The splice condition was wrong twice.** Narrow (does the program call a
  ported name) left `apy_from_int` undefined for dynamic programs that never
  built an integer directly. Broad (does this link the runtime C) fired for
  anything that merely prints, and 101 tests objected to carrying ten runtime
  functions and a 2 KB global they never reach. The switch is per-BUILD and
  reads the module.
* **Then it inverted to `multiple definition`,** because `test_endtoend.py`
  assembles its own link and built a runtime without being told what the
  program supplies -- the same trap that file's header already warns about one
  level up.

### Two things about the tree, learned here

`tests/runner.py` and its 1,932 cases drive the LEGACY CLI (`asmpython <file>`,
no subcommand) and score 0/1932 against this tree regardless of any change.
The live multi-path corpus is `tests/asmpython/integration/test_differential.py`.

An installed third-party plugin can fail a backend from outside the tree. A
`CompilerPatch` wrapping `Lowerer.run` to declare its own runtime symbols
declares them for EVERY program -- and because `run` drops unused externals
inside itself, anything the wrapper appends survives the drop. The C backend
shrugs; the JVM backend checks every external against what it can define and
refuses. Worth knowing before reading a JVM failure as a regression.

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
* MEASURE A COMMIT, NOT A WORKING TREE. The conformance harness freezes `src/`
  before it starts (`tests/harness/snapshot.py`), so a run started before an
  edit scores the tree as it was -- which is the behaviour you want, and is
  also how a number gets attributed to the wrong work. Stage 2 was measured on
  a detached worktree at HEAD for that reason, and because the working tree
  held unrelated uncommitted changes that would otherwise have been in the
  number.

## Measured

Each stage against the oracle, on a clean checkout of the commit named:

| stage | commit | spec+cpython |
| --- | --- | --- |
| 1, the subset | `2f33bbb7` | 1668/1668 (100.0%) |
| 2, the floor | `26201014` | 1668/1668 (100.0%) |
| 3, the integer cell | `564c2c77` | 1668/1668 (100.0%) |
| 3b + 4, arithmetic and the allocator | `ec4fb7a1` | 1668/1668 (100.0%) |

Stage 2's had teeth because the floor's C goes into `host_functions()`, which
every C build and every machine-backend link passes through -- a floor that did
not compile would have scored 0 rather than slightly less.

**Stage 3's is the one that means the most.** `apy_from_int` is the runtime's
most-called constructor: every integer literal, every loop counter and every
length in all 1,668 programs goes through it. A suite that passes with it
ported is a suite in which subset-written code built every integer that was
compared against CPython.

And stage 4 widens that from integers to everything: `apy_alloc` is the only
place objects come from, so a full-marks run means **every object in all 1,668
programs** was handed out by the arena in `runtime/arena.py`, and every integer
`+`, `-` and `*` entered the subset's fast path before reaching any C.

## What stage 5 has to solve first, and it is not a kind

The document's order is "kind by kind, largest surface first: str, list, dict".
Two prerequisites are hidden inside that and are cheaper to name here than to
discover halfway through one.

**The allocator does not survive `list`.** Stage 4's arena is correct because
cells are immortal -- checked, not assumed. A string's bytes are immortal too,
so `str` needs nothing new. A LIST'S ITEMS ARE NOT: `v.q.items` is `realloc`d
on every growth and is the one allocation in the runtime that is genuinely
freed. A bump pointer cannot resize or reclaim, so `list` forces size classes
and a free list -- which is a stage, not a kind.

**`func_addr`/`call_ptr` are still deferred**, three times now and honestly
each time: laying out an object does not need them, dispatching on one does.
`str` dispatches on a kind tag and does not need them either. Classes,
generators and every dunder lookup do, so they land before those and after the
kinds that are tag-dispatched.

So the order that follows from what the code can actually do:

    str  ->  the allocator upgrade  ->  list, dict  ->  func_addr/call_ptr
         ->  classes, exceptions, generators

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
