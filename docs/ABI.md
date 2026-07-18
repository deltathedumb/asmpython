# asmpython Binary ABI — v1

Status: describes CURRENT, VERIFIED behavior as of asmpython 3.14.0-preview.
This is the first version of this document; nothing before it was formally
specified. See "Versioning" below for what stability this document promises.

## Scope

This document specifies asmpython's **binary/machine-code-level** contracts:

1. The calling convention `@assembly_func` bodies (raw inline NASM) must
   follow to interoperate correctly with compiled asmpython code.
2. The in-memory layout of asmpython's runtime value types (`int`, `float`,
   `str`, `list`, `dict`, class instances).

It does NOT cover:

- The `Backend` / `Linker` / `mlang.Config` Python-level plugin registration
  APIs (`asmpython.backend`, `asmpython.linker`, `asmpython.mlang`). Those
  are a *Python API* stability question, tracked separately from a binary
  ABI.
- `--type library` as a per-function callable C ABI. As of this version,
  it is not one — see "Shared-library output" below for exactly what it is.
- The `.asmpkg` assembly-package format. It existed in earlier development
  and was removed; there is nothing to specify.
- The System V AMD64 / Microsoft x64 ABIs asmpython's own FFI bindings
  (`asmpython/stdlib/*.py`'s `Func(...)` entries) call INTO, for libc/
  msvcrt. asmpython does not define these; see your platform's own ABI
  documentation. asmpython's FFI dispatch just implements them correctly
  (int/float/str arguments and return values only, as of this version).
- The withdrawn compiler-extension system (`asmpython.extend`) — see
  `archived/extensions/` and `docs/api.html#api-extend`.

## IMPORTANT — backend divergence

asmpython has two production codegen backends: `legacy` (NASM-text,
`asmpython/_compiler/codegen.py`) and `x86-64` (direct-to-object SSA IR,
`asmpython/_backends/x86_64/`). **`x86-64` is the default** for
`--target windows` and `--target linux`.

**`@assembly_func` (Section 1 below) is honored ONLY by `--backend legacy`.**
Under the default `x86-64` backend the compiler now refuses to build a
program using `@assembly_func` at all (see `driver.py`'s explicit guard) —
until that guard was added, an `@assembly_func`-decorated function
compiled WITHOUT ERROR under `x86-64` but silently discarded the NASM body
and fell through to `return 0`, confirmed by compiling
`tests/cases/75_assembly_func.py` under both backends: `legacy` produces
the correct `42 / 7 / 100`; `x86-64` produced `0 / 0 / 51` before the guard
existed. If your program uses `@assembly_func`, pass `--backend legacy`
explicitly. (Freestanding targets select `legacy` automatically since
`x86-64` doesn't support them yet, so this only bites `--target
windows`/`linux` users who don't pass `--backend` explicitly.)

Runtime data-type layouts (Section 2) are NOT backend-divergent: both
backends read and write the same field offsets, either directly (the
`x86-64` backend's IR lowering explicitly mirrors `codegen.py`'s list
offsets) or via the `_abi_*` shim layer (`asmpython/_runtime/abi_shims.asm`,
`abi_shims_linux.asm`) that adapts the legacy runtime helpers' ad-hoc
internal calling convention to the standard ABI the `x86-64` backend's
`call` IR op expects. That shim is purely an internal bridge between
asmpython's own two backends — it is not itself a public contract, and
this document does not specify its internals; it specifies the resulting
LAYOUTS, which are.

## 1. `@assembly_func` calling convention

(`--backend legacy` only — see divergence note above.)

An `@assembly_func`-decorated function's Python signature (parameter count,
implicitly `int`-typed today) determines how the caller marshals arguments;
its docstring is raw NASM emitted verbatim under the function's mangled
symbol, with NO compiler-generated prologue or epilogue. The author has
full control and full responsibility for the calling convention.

### Argument registers

| Target                    | Arg 1 | Arg 2 | Arg 3 | Arg 4 | Arg 5 | Arg 6 |
|----------------------------|-------|-------|-------|-------|-------|-------|
| Linux (System V AMD64)     | rdi   | rsi   | rdx   | rcx   | r8    | r9    |
| Windows (Microsoft x64)    | rcx   | rdx   | r8    | r9    | (stack, 5th+) | |

- All current asmpython function parameters are `int` (64-bit signed); there
  is no float-argument or string-argument `@assembly_func` calling
  convention documented here because the language doesn't support
  non-int `@assembly_func` signatures yet (see `about.md`'s "What does NOT
  work yet" — function signatures are int-in/int-out only, this applies to
  `@assembly_func` too).
- On Windows, the standard 32-byte shadow space below `rsp` must be
  respected by the body if it calls anything else.
- 16-byte stack alignment at any `call` the body itself issues must be
  maintained by the body's own prologue/epilogue — none is synthesized.

### Return value

- **Integer/pointer result in `rax`.** The body must `ret` with its result
  in `rax`. There is no other return-value convention available today
  (matches the language's int-only-signature restriction).

### Symbol naming

- A free function's `@assembly_func` body is emitted under its Python name
  verbatim, EXCEPT `def main()`, which is mangled to `userfn_main` (`main`
  itself is reserved for the C-runtime entry point asmpython generates).
- A method's `@assembly_func` body is emitted under `ClassName__method`
  (two underscores; `__init__` mangles to `Class____init__`, i.e. four
  underscores — `Class__` + `__init__`).
- User-defined function/method symbols are NOT marked `global` in the
  emitted NASM by default — they are visible to other code compiled into
  the SAME program/object, not to an external linker. (This matters
  directly for "Shared-library output" below.)

## 2. Runtime data-type layouts

Authoritative source: `asmpython/_compiler/codegen.py`'s `Codegen.LIST_*`/
`Codegen.DICT_*` class attributes (verified directly against source, not
against `about.md`'s summary table, which is imprecise — see "Corrections
to prior documentation" below).

| Type    | Representation                                                | Size    |
|---------|----------------------------------------------------------------|---------|
| `int`   | 64-bit signed integer, register or 8-byte stack slot            | 8 bytes |
| `float` | IEEE-754 double, XMM register or 8-byte stack slot               | 8 bytes |
| `str`   | Pointer to a nul-terminated byte string (`.rodata` or heap)      | 8 bytes (pointer) |
| `list`  | Pointer to a stable 24-byte header (below) + heap element buffer | 8 bytes (pointer) |
| `dict`  | Pointer to a stable 40-byte header (below) + heap slot/order buffers | 8 bytes (pointer) |
| class instance | Identical representation to `dict` (see below)             | 8 bytes (pointer) |

### `list` header (24 bytes, stable across mutation)

| Offset  | Field        | Meaning                                   |
|---------|--------------|--------------------------------------------|
| `+0`    | `capacity`   | number of slots in the element buffer (int64) |
| `+8`    | `length`     | number of populated slots (int64)          |
| `+16`   | `buffer_ptr` | pointer to heap-allocated array of int64/double-sized elements |

Element `i` is at `[buffer_ptr + i*8]`. `append` may reallocate
`buffer_ptr`; the 24-byte header itself never moves, so a variable holding
the header pointer stays valid across growth. Tuples share this exact
layout (no separate tuple representation).

### `dict` header (40 bytes, stable across mutation)

| Offset  | Field         | Meaning |
|---------|---------------|---------|
| `+0`    | `capacity`    | slot count, always a power of 2 (int64) |
| `+8`    | `length`      | live entries (int64) |
| `+16`   | `tombstones`  | deleted-but-unreclaimed slots (int64) |
| `+24`   | `slots_ptr`   | pointer to heap buffer of 16-byte slots |
| `+32`   | `order_ptr`   | pointer to heap buffer of `capacity` key pointers, insertion order |

**Correction vs. prior documentation:** `about.md`'s summary table describes
a 32-byte, 4-field dict header (`[cap, len, tombs, buf_ptr]`). The actual,
current implementation (`codegen.py`'s `DICT_CAP_OFF`/`DICT_LEN_OFF`/
`DICT_TOMB_OFF`/`DICT_BUF_OFF`/`DICT_ORDER_OFF` = 0/8/16/24/32) is 40 bytes
with 5 fields — `order_ptr` (insertion-order tracking, giving CPython
3.7+-style dict iteration order) is real, load-bearing, and was
undocumented in `about.md`. This spec supersedes that table.

Each slot is 16 bytes: `key_ptr` at `+0` (`0` = empty, `1` = tombstone,
otherwise a nul-terminated string pointer the dict owns/strdup'd),
`value` at `+8` (a raw int64 word — floats are bit-cast into/out of this
word, not boxed).

### Class instances

A class instance IS a `dict` (identical 40-byte header, `str -> int64`
slots) — there is no separate object header, no vtable, no class-identity
pointer. This is why method dispatch is static (resolved at compile time
from the receiver's declared type, per `about.md`) rather than virtual:
there is no runtime type tag to dispatch on. A freshly-constructed instance
starts with `capacity=8` slots (64-byte slot buffer) and a 64-byte order
buffer.

## 3. Shared-library output (`--type library`)

**Current status: `--type library` is a container-format switch, not a
per-function external calling convention.** Verified by compiling and
inspecting a two-function module:

- The ONLY symbol asmpython emits as `global`/exported by default is the
  C-runtime startup wrapper (literal symbol `main`, which parses
  `argc`/`argv` and calls the user's `def main()` — itself mangled to
  `userfn_main`). A `--type library` build's DLL/shared-object export table
  contains exactly this one entry.
- User-defined top-level functions and methods are emitted as ordinary,
  non-global NASM labels. `gcc -shared -Wl,--export-all-symbols` (what
  `--type library` passes on Windows today) does not promote them — it has
  no effect on symbols that were never marked global in the object file.
- **There is currently no mechanism (`@export`, `@public`, or similar) to
  expose an individual asmpython function to an external caller.**

Practically: a `--type library` build today is useful as an alternate
packaging format for the SAME whole-program entry point (e.g. embedding
an asmpython program's `main()` behavior inside a host process that calls
it once, the way a plugin loader might invoke a single well-known
initialization symbol) — not as a way to call arbitrary asmpython
functions by name from other languages. A future "exported function"
feature is a prerequisite for that; it does not exist yet and this
document makes no promises about its eventual shape.

## Versioning

This document's own version (`ABI 1`, above the title) is independent of
the `asmpython` package version (currently `3.14.0-preview`) and of any
future ABI document version (`ABI 2`, etc.).

- **ABI 1 commitment:** none of the layouts or conventions above are
  guaranteed frozen. This document is a snapshot of CURRENT, VERIFIED
  behavior. Any change to `Codegen.LIST_*`/`DICT_*` offsets, the
  `@assembly_func` register convention, or which backend supports
  `@assembly_func` at all, MUST be accompanied by (a) an update to this
  document bumping to `ABI 2` (or later), and (b) a `CHANGELOG.md` entry
  under a new `### ABI` subheading describing the break.
- This is a deliberately modest promise given asmpython's actual release
  history: three real point releases (1.0.0-1.2.0) plus an in-progress
  jump to CPython-version-aligned `3.14.0-preview`, including a backend
  rewrite that already broke one binary contract (`@assembly_func` on the
  new default backend) without a corresponding ABI-level changelog
  callout at the time. A stronger promise (e.g. "frozen across a MAJOR
  version") is not one the project's current pace could keep honestly;
  this document exists so the NEXT such break is deliberate and
  documented, not silent.
