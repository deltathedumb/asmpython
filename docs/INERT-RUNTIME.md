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

## Where this stands

**Stages 1 to 4 are done and measured. Stage 5 is under way: the string
cell and its length, code-point and search families are ported, the allocator
upgrade that stage 5 named as its own prerequisite is done, and the SEQUENCE
CELL has followed it. Stage 6 is not started.**

**Stage 5b was not in the plan and was found by walking into it.**
`apy_seq_push` was written, worked, and was refused: it answers `None`,
`apy_none()` was a C function, and the ported runtime may reach for the
platform floor and its own `_slow` halves and nothing else. The singleton
cells -- `None`, `True`, `False`, `Ellipsis`, `NotImplemented` -- are the
first SHARED STATE rather than shared code here, and they move as one piece or
every `is` spanning the two halves answers False. **They are ported now**, the
C's two direct uses of `&apy_none_cell` go through `apy_none()`, and append is
back. Every kind that answers None was waiting on that and no longer is.

| | | |
| --- | --- | --- |
| 1 | the machine subset | widths and memory in the static path |
| 2 | the platform floor | **3 functions**, down from 5, and none language-aware |
| 3 | the integer cell | construction, and arithmetic as a split |
| 4 | the allocator | every object in a program, from one arena in the subset |
| 5 | kind by kind | **under way** -- `str`'s cell, length, codes and search; the sequence cell; and the allocator upgrade below |
| 5b | the singleton cells | **done** -- one None, owned by IR; the C's own two uses redirected through the accessor. `apy_stop` joined them as a sixth |
| 5c | indirect calls | **done** -- `funcaddr`/`callptr` in the subset; every backend already implemented the opcodes |
| 5a | the buffer allocator | size classes and free lists, which `list` and `dict` need before anything else |
| 6 | delete the C | not started; and see the note on `objects_host.py` below |

Each stage is recorded below in a "What stage N landed" section, including what
it could NOT do, which twice turned out to be the more useful half.

**The headline number has moved for the subset and not yet for the language.**
A backend compiling the statically typed subset owes three functions instead of
five, and none of the three knows what a Python value is. A backend compiling
DYNAMIC Python still owes 423 exported symbols minus the 53 now written in IR
-- the mechanism to move the rest exists, is measured, and is the whole of
stage 5. **370 remain**, of which 38 are fully replaced and 15 keep a C
`_slow` half. That number is the honest size of what is left, and it is now
moving in batches rather than one kind at a time: the walls are down, so what
governs the rate is how many functions depend only on what is already ported.

**WHAT UNBLOCKED THE BATCHES.** Three things had to land before any of this
was cheap -- the buffer allocator (5a), the singleton cells (5b) and
`funcaddr`/`callptr` in the subset. With those in place, a survey of the
remaining C found 86 functions with no `static` helper in their way and
fourteen lines or fewer, which is the seam the last three batches came out
of: the ASCII predicates, the closure cell, the sentinel and identity, the
kind predicates, and `object`'s own defaults.

**What the C runtime is now: still supported, no longer required.** That
distinction is the point of the whole exercise. `--object-runtime c` uses the
hand-written C for all of it, exactly as every build did before any of this,
and `Backend.object_runtime` is the same choice one function at a time. Both
are tested, because an untested opt-out stops working the first time the ported
set grows -- and it grows every stage from here.

## What exists now, and how many times

| | lines | language | consumed by |
| --- | --- | --- | --- |
| `objects/c/` | 16,284 | C, in nineteen parts concatenated in source order | the C backend, and the machine backends via a C toolchain |
| `objects/c/unicode_table.py` | 909 | C, generated | spliced into the above at `/* @UNICODE_TABLE@ */` |
| `ir/objects_host.py` | 8,583 | Python | the IR interpreter |
| a JVM equivalent | 0 | — | nothing; `BackendUnsupported` |

**The runtime is written twice and needed a third time.** That is not an
accident of history, it is what the current design requires: `objects/csource.py`
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

What has to be added to the static path, and what became of each:

* **DONE** (stage 1) the machine types as annotations -- `i8 i16 i32 i64 u8 u16
  u32 u64 f32 f64 ptr`, which the IR already has and `analysis.BY_NAME` did not
  expose
* **DONE** (stage 1) memory intrinsics lowering straight to IR ops: `load`,
  `store`, `offset`, `alloca`, and `sizeof`-style constants
* **NOT YET** `func_addr`/`call_ptr` for the dispatch tables the runtime uses
  (18 function-pointer types in the C today). Deferred three times, honestly
  each time: laying out an object does not need them and dispatching on one
  does, so they land before classes and generators and after the kinds that
  dispatch on a tag.
* **DONE** (stage 3) module-level mutable storage. `global_addr` existed in the
  IR; the static path now surfaces it as `reserve(name, bytes)` -- static,
  zeroed, for the whole run, which is what a cache needs and what `alloca`, a
  parameter and `plat_heap` between them could not give.

None of it is new IR. All of it is surfacing IR the backends already lower --
which held: not one opcode was added for any of stages 1 to 4.

### And it deletes `objects_host.py` -- but not the way this said

This was the argument that decided it. If the runtime is IR, the IR interpreter
runs THE SAME RUNTIME the C backend runs. `ir/objects_host.py`'s 8,583 lines
stop existing, and every drift class listed above becomes structurally
impossible rather than something a corpus has to keep catching.

**The conclusion holds and the schedule does not, and stage 3 is where that was
found.** The sentence above quietly assumes the deletion is incremental -- port
a function, the interpreter picks it up, the host copy of that function stops
being reached. It is not. `objects_host.py` represents an `apy_value` as a
HANDLE into a Python-side table and the ported code represents one as an
ADDRESS, so the two cannot be mixed at all: a ported `apy_from_int` hands back
something every unported function rejects.

So `objects_host.py` goes in ONE STEP, at stage 6, once every kind it claims is
ported -- and until then the interpreter keeps it and is the ORACLE the compiled
paths are measured against. That is a better arrangement than the one intended
here, and it was not designed; see "What stage 3 landed".

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

`objects/floor.py` holds the contracts and the C implementation; the JVM's is
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

### What stage 3 cost

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

## What stages 3b and 4 landed

### The split, which is how every remaining kind arrives

`apy_add` is polymorphic over eighteen kinds, so porting it whole means porting
all of them -- the all-or-nothing this document exists to avoid. So the C body
is RENAMED and the name is left to the IR (`objects/csource.split_c`):

    APY_API apy_value apy_add(apy_value, apy_value);            <- IR
    APY_API apy_value apy_add_slow(apy_value a, apy_value b){   <- the C

The subset answers `int x int` and hands back everything else. **Only the
definition is renamed**, so the runtime's own hundred-odd `apy_add(...)` calls
enter the fast path too -- a port that accelerated only the frontend's calls
would leave most of the work where it was.

A FAST PATH MAY DECLINE; IT MAY NOT ANSWER WRONGLY. Overflow is exact for add
and sub. For mul it is deliberately conservative: the exact test divides the
product back, and division here is PYTHON's, which floors, so it would be wrong
for negative operands in a way that shows on only some of them. Two 32-bit
operands cannot overflow a 64-bit product, which is a test with no division in
it and no signedness to get wrong.

### One allocator, and why a bump pointer is enough

`apy_alloc` in the C is now a four-line wrapper around `apy_obj_alloc`, which
`runtime/arena.py` defines -- so **every object in a compiled program**, the C
runtime's and the ported code's alike, comes from one place. A `malloc` per
cell became a pointer increment, and the platform floor is hit once per
megabyte rather than once per integer, which is what stage 2's three-function
claim needed to survive contact with real programs.

**It is only correct because nothing frees a cell, and that was checked.** Of
the 51 `free()` calls in the C runtime, every one releases a BUFFER -- a list's
item array, a formatting scratch, a split's parts -- and not one releases an
`apy_obj`. A test says so and is what notices if it stops being true.

### What stages 3b and 4 cost

* **`apy_cell_new` was already taken** -- it is the CLOSURE-CELL constructor --
  and a blanket rename hit both, reproducing the collision under a new name.
  Renamed only the three occurrences the split introduced, each identified by
  its signature rather than by its name.
* **`_declare_only` stopped at a forward declaration** and reported "declared
  but not defined" about a function defined thirty lines below.
* **Two signature tables had drifted.** Lowering parsed the C itself instead of
  reading analysis's table, so a call the analyser accepted was lowered with
  nothing declaring it: `call to unknown function 'apy_mul_slow'`.
* **The runtime had to become ONE compilation unit**, because its files call
  each other and per-file compilation made every cross-file call unknown.

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

    str  ->  the allocator upgrade  ->  the sequence cell  ->  the singleton
         cells  ->  func_addr/call_ptr  ->  [ALL DONE]
         ->  the rest of list, dict  ->  classes, exceptions, generators

The singleton step was not in the original order and was found by walking into
it -- see `runtime/list_cell.py`, which records the refusal that produced it.
Everything left of `func_addr/call_ptr` is now done.

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


## What stage 5a landed: the buffer allocator

**The prerequisite this document named before it named a kind.** "The allocator
does not survive `list`" -- stage 4's arena is a bump pointer, correct because
cells are immortal, and a list's `v.q.items` is the one allocation this runtime
genuinely frees. `runtime/blocks.py` is size classes and free lists over the
same arena: `apy_alloc_block`, `apy_realloc_block`, `apy_free_block`.

**Two allocators and not one, which was the design question.** Rounding every
allocation up to a size class costs a CELL 40% -- 152 bytes into a 256-byte
class -- to serve the one kind of allocation that is ever freed. So
`apy_alloc_bytes` keeps its exact-fit bump for immortal things and blocks get
classes. Both draw from the same arena, so the platform floor is still three
functions and still gets hit once a megabyte.

**The size travels with the pointer, and that is a real cost.** `free(p)` needs
no size because malloc writes a header before every block; a size-classed
allocator over a bump arena has none to read, and adding one would cost eight
bytes on every buffer to store what every caller already knows. So the caller
passes the length it asked for -- a contract a caller can break, where malloc's
cannot be. It is checkable: a wrong size puts the block on the wrong list and
the next allocation of that class is handed memory that is too small, which the
corpus finds at once.

**Measured, on a program that builds and releases one size class 200 times:**

| | peak |
| --- | --- |
| the IR block allocator | 4.5 MB |
| the C's malloc/free (`--object-runtime c`) | 5.3 MB |
| the same, if nothing were reused | ~48 MB |

**And a negative finding worth more than the positive one.** Isolating that
measurement showed a program that builds a 4096-element list 4000 times and
NEVER releases it costs 147 MB, against 24 MB when it does. That gap is not
this allocator: nothing frees a dropped list's buffer, because there is no
garbage collector, and the C runtime leaks it identically. What the free lists
recover is exactly what is handed back, which is what they claim. A collector
is a different stage and this document does not schedule one.

**What it does not do.** No coalescing, no splitting, no return to the
platform: a block is reused at its own class or not at all. That is enough
because the only thing that frees here GROWS BY DOUBLING, so every block it
releases is exactly the size the next one up asks for. Past the last class
(2**34) a block is dropped rather than tracked.

**The C keeps its version**, over malloc, and `--object-runtime c` uses it --
the same arrangement every stage has had, and the reason both are tested.

## Host services: the other end of the same project

This document is one half of an argument. It shrinks what a backend owes by
writing the runtime in the subset -- 24 of 418 symbols so far, 394 to go. The
other half is `objects/hostsvc.py`, which NAMES what a backend owes so that it
stops growing.

**The two meet at a number.** When stage 6 lands, a backend owes the platform
floor and nothing else -- and "nothing else" has to be written down or it is
not a claim. So `hostsvc.py` is one table of operations with fixed signatures,
the floor is its mandatory `core` group, and everything a real program needs
beyond the floor is an OPTIONAL group a backend declares: a filesystem, a
clock, entropy, an environment, a network, a character database.

**What forced it was `pathlib`.** That module reaches `_open`, `_read` and
`GetFileAttributesA` through `ctypes`, which `frontends/python/cffi.py`
resolves at COMPILE time -- a promise to the linker rather than a `dlopen`.
That is exactly right for what it is, and it means `pathlib` works on the C
backend and can never work on the JVM, which has no linker and no `_open`. The
symbol names are the problem: `_open` is MSVC's spelling, `open` is POSIX's,
`java.nio` is neither.

**Optional and declared, not a bigger floor.** Stage 2's whole achievement was
five functions down to three. A mandatory thirty would undo it and would make
a target without a filesystem impossible to write a backend for. So a program
using a group its backend has not got is refused at COMPILE time naming the
group -- not as an undefined symbol at link time naming an object file, and
not as a wrong answer at run time.

**The floor's own rule is inherited unchanged.** Nothing may know what a
Python value is. Every signature is machine words and pointers to bytes, and
`tests/asmpython/integration/test_hostsvc.py` asserts it, because the moment
one takes a `str` every backend implementing it owes the LANGUAGE rather than
the machine -- which is the argument `objects/floor.py` makes for why the
floor is three functions and not the five it used to be.

**Not an opcode, and the floor is the precedent.** `plat_write` is an ordinary
`Op.CALL` of an external symbol; nothing in the instruction set knows it
exists. An opcode would have to be implemented by five backends plus the
verifier, printer, liveness and interpreter -- eleven places -- to express
what a call with a signature already expresses. An opcode buys a new SHAPE of
instruction, and these are not a new shape.

**What it cost to find out, and it is the same trap as before.** The first
version had the C backend `#include <sys/stat.h>` and `<direct.h>` for the
file group. Both drag in `<io.h>` on MinGW, which declares `_open`, `_read`,
`_write` and `_close` -- exactly the names a `ctypes` program declares for
itself -- and two prototypes for one symbol do not compile. Adding them
re-created, from the other side, the obstacle `cffi.py` documents and this
layer exists to retire, and it broke every compiled program that reaches libc
through `ctypes`, `pathlib` included. So the file group includes no header
beyond `<errno.h>`: it declares the five platform functions it needs itself,
and detects a directory with `opendir` rather than `stat`.

**Where it stands.** The C backend provides `file`, `time`, `random` and
`env`; the IR interpreter provides the same four; `net` and `text` are named
in the table and implemented nowhere, which is an honest absence a program
gets told about. The JVM backend provides none and refuses cleanly.

**`pathlib` IS MIGRATED, and it is the layer's first real customer.** It
needed the dynamic path to reach a host service the way `_dyn_ctypes_call`
reaches a native one -- every bundled module is untyped Python, so a
static-path-only layer was no use to the standard library at all. That is
`analysis._dyn_hostsvc_call` and `dynamic._dyn_hostsvc_call`: unbox each
argument to a machine word, one `Op.CALL`, box the answer back. Simpler than
the `ctypes` version beside it, because every host service answers `i64` and
takes only `i64` and `ptr`.

What came out of `bundled/pathlib.py` with the `ctypes` block is every
platform constant it had: `_O_BINARY` was 32768 because that is MSVC's number,
`_S_IWRITE` was 128 for the same reason, and `_INVALID` was
`GetFileAttributesA`'s sentinel. A module that had no business knowing which
operating system it was on no longer does.

**The measurable difference is what the JVM says.** Before, `pathlib` on any
backend but C was not a diagnostic but an impossibility -- `_open` is a symbol
only a linker can find. Now:

    error[E9103]: the jvm backend cannot compile this program for jvm
      = note: this program needs host services the jvm backend does not
        provide: 'file' (for host_file_open).

Still a refusal, and now an actionable one: it names work someone can do,
rather than a property of the design.
