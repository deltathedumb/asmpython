# asmpython IR design (2.0.0)

Status: implementation in progress. This document is the reference for
the codegen rewrite that makes ARM64 (and any future architecture) a real
second backend instead of x86-64 being hardcoded into the shared compiler
core. See `roadmap.md` for why this is versioned 2.0.0 rather than 1.3.0.

**Progress as of 2026-06-17** (see git log for the actual commits):
`ir.py` (the data model) and `ir_builder.py` (ergonomic construction
helpers) are done and stable. `ssa_build.py` (AST -> IR construction,
step 2 of the migration strategy below) has real, hand-verified-correct
implementations for: int/float/string literals, plain local reads/
writes, the *complete* primitive int/float arithmetic surface (`+ - *
/ // % ** & | ^ << >>`, all with correct zero-division checking and
Python floor/NaN semantics — int `//`/`%` get the truncate-toward-zero
-> floor-toward-`-inf` adjustment via real ICMP/CONDBR + a merge phi;
float `//` is divide-then-libc-`floor()`, `%` is libc `fmod()`, `**`
is an int multiply-loop or libc `pow()`), unary ops, comparisons
including chained comparisons (`a < b < c`), if/while/break/continue,
calls to plain user-defined functions, and `for x in range(...)`.

Several real bugs were caught and fixed during this work — see the
commit history on `ssa_build.py` for details, since they're
informative about what kinds of mistakes this style of builder is
prone to: an empty-block validation failure in the while-loop builder;
a missing "stop building once the current block is terminated" guard;
and a missing zero-division check on float `/` (present on `//`/`%`
from the start, but the original `/` implementation predated that
pattern being established and the gap wasn't caught until revisiting
the area for `//`/`%`).

**Explicitly not yet done**: zero-division-check/exception-raising's
*general* mechanism (the `ZeroDivisionError` case is hand-wired
directly via `_runtime_raise`+`BUILTIN_EXC_IDS`, not a reusable
"raise this exception" builder — `try`/`except`/general `raise` still
need their own design), string/list/dict/set/tuple *operations*
(methods, literals, indexing — only string literals as *values*
exist so far), f-strings, classes/methods/dunder dispatch, closures,
generators, match statements, and `for` over anything but `range(...)`.
Per the "migration strategy" section below, most of these are expected
to be a mechanical CALL/RAW_ASM wrapping pass (they already call
`_runtime_*` helpers in the existing direct-emission codegen, not
primitive arithmetic) rather than needing new IR semantics — but that
pass, the register allocator, the X86_64Target lowering, full
test-suite parity, and ARM64 lowering are all still ahead. The
complete primitive-arithmetic surface plus full control flow is a
meaningfully larger fraction of the *design risk* than of the
remaining *volume* — the mechanical wrapping pass ahead is still
substantial work, just lower-risk per node type than what's landed
so far.

## Why this exists

A 2026-06-17 codebase survey of `asmpython/_compiler/codegen.py` (~14,000
lines) found:

- ~1,203 call sites in the *shared* `Codegen` base class directly embed
  literal x86-64 register names (`rax`, `rbx`, `rcx`, ...) in emitted
  instruction text — not in the target-specific subclasses, in the part
  that's supposed to be architecture-agnostic.
- There is no register allocator. The convention is "the current
  expression's value lives in `rax` (int/pointer) or `xmm0` (float);
  `rbx`/`rcx`/`rdx` are ad hoc scratch within a single expression's
  codegen." This works because it's never live across more than one
  nested `gen_expr` call at a time, but it's load-bearing throughout
  `gen_expr`/`gen_stmt`/`_gen_binop`/`_gen_call` and every helper they call.
- 73 `_runtime_*` helper functions (dict/list/string/exception machinery)
  are hand-written x86-64 assembly text with a bespoke internal calling
  convention (args in `rax`/`rbx`/`rcx`, not the platform C ABI). None of
  them go through a higher-level emission path — every one needs an
  ARM64-native rewrite regardless of what the IR looks like.
- The existing abstraction seam (`_arg_reg`/`_assign_arg_regs`/
  `emit_func_prologue`/`_caller_shadow_space`, ~48 methods total) only
  covers *calling-convention bookkeeping* (which register holds argument
  N, how much shadow space the ABI needs) — never instruction selection
  or register allocation. It was sufficient for Win64-vs-SysV (same ISA,
  different calling convention) but does nothing for a different ISA.
- Two semantic (not just syntactic) gaps: x86's `cqo`+`idiv` signed-division
  idiom has no ARM64 equivalent (`sdiv` + `msub` reconstruction needed),
  and `ucomisd`'s unordered-compare flag combination doesn't map 1:1 onto
  ARM64's `fcmp`+NZCV for NaN-involving `!=` comparisons.

Conclusion: ARM64 needs a real intermediate representation that x86-64 is
re-expressed on top of, not a parallel target subclass. This is why the
release is 2.0.0, not 1.3.0 (see `roadmap.md`'s decision criterion).

## Scope and non-goals

**In scope for the first cut:**
- An SSA-form IR covering everything asmpython's language subset needs:
  integer/pointer arithmetic, float arithmetic, comparisons, calls
  (direct and indirect), control flow (branches, loops via blocks),
  loads/stores to the frame and to heap structures.
- A linear-scan register allocator (not graph-coloring — see "Register
  allocation" below for why).
- x86-64 lowering that reproduces today's Windows/Linux output closely
  enough to pass the existing 454-case test suite.
- ARM64 (AArch64) lowering for Linux (the realistic first ARM64 host;
  Windows-on-ARM is a later target, not blocking this cut).

**Explicitly not in scope for the first cut:**
- Optimization passes beyond what's needed for correctness (constant
  folding, dead code elimination, etc. are a later pass over the IR —
  the IR's existence is what makes them tractable, but writing them is
  separate work, tracked in `roadmap.md`).
- Rewriting all 73 `_runtime_*` helpers for ARM64 in the same pass that
  introduces the IR. The IR cutover and the ARM64 runtime-helper port are
  sequenced (IR + x86-64 parity first, then ARM64 lowering + runtime
  helpers) — see "Migration strategy."
- Freestanding (bare-metal) targets staying on the IR path. They have
  their own constraints (no libc, raw Multiboot/BIOS boot) and the x86
  freestanding code is small enough to leave on direct emission rather
  than risk it during the cutover. ARM64 freestanding (Raspberry Pi) is
  a *later* roadmap item that will target the IR once it exists, but
  porting `target_freestanding.py`/`target_freestanding16.py` to the IR
  is not part of this change.

## IR shape: SSA

Values are defined exactly once (`%v3 = add %v1, %v2`), control-flow joins
take phi nodes. This is the standard choice for a compiler that also wants
optimization passes later (the roadmap's "Performance and optimisation"
item) — SSA makes dead-code elimination, constant propagation, and
common-subexpression elimination straightforward in a way three-address
code without SSA doesn't.

Cost: needs an SSA-construction step (straightforward here since asmpython
source already goes through a structured statement walk with explicit
scopes — no `goto`, no irreducible control flow to worry about) and an
out-of-SSA / phi-elimination step before register allocation (linear-scan
register allocators conventionally consume a phi-eliminated or
interval-based representation; see below).

## Value/type model

Every asmpython runtime value today is an untyped 8-byte slot (the AST's
static type tracking is compile-time only; nothing in the generated code
distinguishes an int from a pointer from a boxed value at the bit level
except by convention). The IR makes three value kinds explicit, because
ARM64's calling convention and instruction set distinguish them more
strictly than x86-64's "everything is a GPR or xmm register" looseness:

- `Int64` — integers and pointers (asmpython doesn't distinguish these
  at the value level either; both are "an 8-byte GPR-class value").
- `F64` — floats (the only float width asmpython has).
- `None`/control — for instructions with no result value (stores,
  branches, calls used only for side effect).

This is a small, deliberately minimal type lattice — it mirrors what the
language actually needs today rather than anticipating types asmpython
doesn't have (no i32/i16/i8 distinctions, no vectors). If freestanding/
GC work later needs narrower integer types, extend then.

## Instruction set (initial)

Grouped by what survey section they replace:

**Arithmetic/logic** (int): `Add`, `Sub`, `Mul`, `SDiv`, `SRem`, `And`,
`Or`, `Xor`, `Shl`, `Shr` (logical), `Sar` (arithmetic), `Neg`, `Not`.
`SDiv`/`SRem` are a single IR op each — the x86 `cqo`+`idiv` vs. ARM64
`sdiv`+`msub`-for-remainder difference is purely a lowering-stage concern,
invisible above the IR.

**Arithmetic** (float): `FAdd`, `FSub`, `FMul`, `FDiv`, `FNeg`.

**Comparison**: `ICmp(pred, a, b)` and `FCmp(pred, a, b)` each producing a
boolean-ish `Int64` (0/1) — predicates `eq/ne/lt/le/gt/ge`. Keeping compare
as a value-producing op (rather than x86-style flags + separate branch)
means the IR itself doesn't model a flags register at all; flag-register
reuse is a lowering-stage peephole opportunity on x86-64, not something
the IR needs to represent. This sidesteps the `ucomisd` unordered-NaN
semantic gap entirely at the IR level — `FCmp.ne` has unambiguous SSA-value
semantics, and *each target's lowering* is responsible for choosing the
right real instruction sequence to match that semantics (x86-64: `ucomisd`
+ careful jcc selection so unordered counts as "ne"; ARM64: `fcmp` + `b.ne`,
which already treats unordered as not-equal — actually the *simpler* side
of this on ARM64).

**Memory**: `Load(ptr, offset)`, `Store(ptr, offset, value)` — frame slots
are just `Load`/`Store` against a fixed `FrameBase` pseudo-value, so there's
no separate "local variable" instruction; this also means register
allocation can decide to keep a hot frame slot in a register across its
live range instead of always round-tripping through memory, which is a
real (if secondary) win over today's "every local always lives in its
`[rbp+N]` slot" behavior.

**Control flow**: `Br(target)`, `CondBr(cond, true_target, false_target)`,
`Ret(value_or_none)`, `Phi(incoming: list[(block, value)])`.

**Calls**: `Call(target, args, is_indirect)` — `target` is either a symbol
name (direct call, today's `extern foo` + `call foo`) or an SSA value
holding a function pointer (today's closure/`import_binary` indirect-call
pattern, already proven to exist in the current codegen per the survey).
`SetjmpFrame`/`Longjmp`-equivalent ops for the exception-handling mechanism
(today implemented via real `setjmp`/`longjmp` libc calls per the existing
`_emit_call_setjmp...`-style hooks) stay as plain `Call`s to those libc
symbols — no new IR op needed, they're ordinary calls with a special
calling convention already handled by the libc-call lowering.

**Escape hatch**: `RawAsm(target_text: dict[str, str])` — an instruction
carrying pre-written assembly text *per target*, used only for the 73
`_runtime_*` helpers during the migration window (see below) so they don't
block the IR cutover on being rewritten for ARM64 immediately. Each target's
lowering substitutes its own string. This is deliberately an "escape
hatch," not a long-term pattern — the goal is for every `RawAsm` site to
eventually become a real sequence of typed IR instructions once there's
time to rewrite that specific helper, but it unblocks shipping x86-64-on-IR
without a hard dependency on every runtime helper being ported on day one.

## Register allocation: linear-scan

Graph-coloring allocators produce better code (fewer spills) but are
substantially more implementation effort and harder to get correct.
Linear-scan is the standard pragmatic choice for compilers at this scale
(LuaJIT, early V8, and most "fast JIT or straightforward AOT" compilers
use it) — it processes SSA values in a single pass over computed live
intervals, assigning registers greedily and spilling the interval that
extends furthest when it runs out, which is simple to implement and
reason about correctness for.

Given asmpython's actual register pressure is low (the current "rax +
3 scratch registers" convention has survived this far precisely because
asmpython expressions don't nest deeply enough to need real allocation
most of the time), linear-scan's typical weakness — leaving some
performance on the table vs. graph-coloring in register-starved loops —
is not expected to matter much in practice. Revisit only if profiling
ever shows otherwise.

## ABI / target-lowering interface

Each target (`X86_64Target`, `Arm64Target`, ...) implements:

- `int_arg_registers()` / `float_arg_registers()` — ordered list, used by
  the same kind of `_assign_arg_regs`-style logic that exists today,
  unchanged in spirit.
- `caller_shadow_space()` — Win64's 32-byte requirement; 0 on SysV/ARM64.
- `lower(instr: IRInstr) -> list[str]` — the actual instruction-selection
  step, one method per IR op (or a dispatch table), returning target
  assembly text lines.
- `emit_prologue(frame)` / `emit_epilogue(frame)` — replaces today's
  `emit_func_prologue`/`emit_func_epilogue`, now driven by the register
  allocator's decisions about which callee-saved registers got used
  (needed for ARM64's stricter callee-saved-register-preservation rules)
  rather than the current fixed `push rbp; mov rbp, rsp; sub rsp, N`.

This keeps the *shape* of today's already-proven target-subclass pattern
(WindowsCodegen/LinuxCodegen → X86_64Target/Arm64Target), just moves the
boundary from "calling-convention bookkeeping only" to "everything below
the IR."

## Migration strategy

No flag-day rewrite. Sequence:

1. **Build the IR module** (`asmpython/_compiler/ir.py`): value/instruction
   classes, SSA construction from the existing AST-walk structure,
   linear-scan allocator, the `Target` lowering interface above. Land
   this with unit-level tests on the IR itself (construct small IR
   graphs by hand, verify lowering output), independent of the rest of
   the compiler — nothing in `driver.py`/`__main__.py` calls it yet.
2. **x86-64 lowering to parity**: implement `X86_64Target.lower()` for
   every IR op, and a `gen_expr`/`gen_stmt` rewrite that emits IR instead
   of direct text. Run *both* the old direct-emission path and the new
   IR path side by side (a `--ir` flag or env var) against the full
   454-case test suite until the IR path matches on every case. Only
   after full parity does the IR path become the default; the direct-
   emission path is deleted once nothing depends on it (the 73
   `_runtime_*` helpers can stay as `RawAsm` during this step — they
   don't need to change for x86-64 parity since they're already
   x86-64 text).
3. **ARM64 lowering**: implement `Arm64Target.lower()`. This is where the
   `RawAsm` helpers become a real blocker — each one needs a hand-written
   ARM64 equivalent before any program using that helper (which, given
   they cover dict/list/string operations, is most real programs) can
   run on ARM64. Port helpers incrementally, prioritized by which
   language features the test suite exercises most.
4. **ARM64 target integration**: `--target linux-arm64` wired into
   `driver.py`, NASM replaced or supplemented with an ARM64 assembler
   (NASM doesn't support ARM64 — likely GNU `as` via the same gcc
   toolchain already used for linking, since gcc ships an ARM64 backend
   too; confirm toolchain availability before this step).

Each step is independently shippable and testable; the repo is never in
a "nothing works" state between steps.

## Open questions to resolve before/during implementation

- **SSA destruction**: does the linear-scan allocator consume phi nodes
  directly (interval-splitting at block boundaries) or do we run a
  conventional "phi elimination via parallel copies" pass first? Leaning
  toward the latter for simplicity in a first cut.
- **Frame layout ownership**: today `FuncInfo`/`_collect_locals` decide
  frame slot offsets ahead of codegen. Does that survive as-is feeding
  the IR (locals are just named `Load`/`Store` targets at known offsets),
  or does the register allocator get to promote some locals to registers
  and shrink the frame? Leaning toward "survives as-is for the first cut"
  — promoting locals to registers is a real optimization opportunity but
  not needed for ARM64 correctness, so defer it.
- **Toolchain for ARM64 assembly — resolved (2026-06-17)**: w64devkit/mingw
  gcc is x86-64-only, and NASM doesn't support ARM64 at all. The working
  pipeline is a WSL2 Ubuntu 24.04 distro with `gcc-aarch64-linux-gnu`
  (provides `aarch64-linux-gnu-gcc`/`-as`/`-ld`, GNU AT&T-syntax ARM64
  assembler — NOT NASM syntax, a real difference from the x86-64 path)
  plus `qemu-user` (provides `qemu-aarch64`, user-mode emulation that
  runs an aarch64 Linux ELF binary directly on this x86-64 Windows host
  without a VM or real hardware). Smoke-tested end to end: hand-written
  `.s` file → `aarch64-linux-gnu-gcc file.s -o out -static` → `qemu-aarch64
  out` → correct output. `driver.py`'s ARM64 path will need to shell out
  to `wsl.exe -d Ubuntu -- aarch64-linux-gnu-gcc ...` (or `-as`/`-ld`
  directly) for the assemble/link step, and tests can run the result via
  `wsl.exe -d Ubuntu -- qemu-aarch64 ...` — both invoked from Windows,
  same pattern as the existing NASM/gcc invocation in `driver.py`, just
  routed through `wsl.exe` as the process boundary. Installed as root
  (`wsl.exe -u root`) since the default WSL user's `sudo` needs an
  interactive password not available to non-interactive automation.
