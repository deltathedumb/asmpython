# 2.0.0 Resume

## Directive
"Continue dev until we hit 2.0.0 ready." 2.0.0 = ARM64 (+ eventually macOS
Intel) platform support, gated on a full SSA/IR rewrite of codegen — see
`docs/IR-DESIGN.md`. User explicitly chose the full IR rewrite over a
smaller register-name-abstraction alternative, and explicitly chose full
AST-node coverage in `ssa_build.py` before any end-to-end compile attempt,
over a minimal-subset-first staged approach. Both settled; don't revisit
without the user re-raising it. Selfhosting (below) is a stretch goal, not
committed 2.0.0 scope.

## Current State: ARM64 IR Rewrite

**Toolchain** (resolved, working): WSL2 Ubuntu 24.04 (`wsl.exe -u root`,
not the default user — its `sudo` needs an interactive password unavailable
to automation) with `gcc-aarch64-linux-gnu` (assembler/linker, AT&T syntax)
+ `qemu-user` (runs aarch64 ELF on this x86-64 Windows host, no VM). Smoke
tested end-to-end with a hand-written `.s` file.

**Design**: `docs/IR-DESIGN.md`. SSA form, two value kinds (`Kind.INT`,
`Kind.FLOAT` — no i8/i16/i32 distinctions), ICMP/FCMP are value-producing
(sidesteps x86 `ucomisd` vs ARM64 `fcmp` unordered-NaN flag differences
entirely at the IR level), linear-scan register allocation, a `RAW_ASM`
escape hatch for the 73 hand-written `_runtime_*` helpers during migration,
four-step migration (IR core -> x86-64 parity -> ARM64 lowering -> driver
integration) that keeps today's direct-emission codegen.py working
throughout.

**Done and committed** (`ir.py`, `ir_builder.py`, `ssa_build.py`, latest
commit `1b1ae842`); working tree clean, 5 commits ahead of `origin/beta`
(not yet pushed as of this pause):

- `ir.py`/`ir_builder.py`: complete, stable data model + construction API.
- `ssa_build.py` primitive/control-flow core (committed earlier): int/
  float/string literals, local read/write, the **complete** primitive
  int/float arithmetic surface (`+ - * / // % ** & | ^ << >>`, correct
  Python floor/zero-division/NaN semantics), unary ops, comparisons incl.
  chained (`a < b < c`), if/while/for(range)/break/continue, calls to
  plain user-defined functions.
- `ssa_build.py` additions this stretch: `BoolOp` (short-circuit, Python
  VALUE semantics — not boolean-normalized), `IfExp` (both arms promoted
  to result type, merged via phi), `ExprStmt`, `Pass`, `AugAssign`
  (reuses `_build_binop` via a synthetic `BinOp`/`Name` pair),
  `MultiAssign` (`a = b = c = value`, plain names only — `TupleAssign`'s
  Subscript/Attr/star-unpack targets still deferred), `Del` (plain local
  `Name` target only — zeros the slot with a raw integer 0, matching
  codegen.py even for float slots since all-zero-bits is +0.0).
- **First real `Op.RAW_ASM` call sites**: `str + str` (-> `_runtime_str_
  concat`), `str == str` / `str != str` (-> `_runtime_str_eq`, `!=` XORs
  the result with 1), and string truthiness (`if s:` — NULL-safe empty-
  string check, written fully branchless using `lea reg, [rel $]` as a
  guaranteed-safe dereference target plus `cmovz`, since the IR has no
  byte-width LOAD and RawAsm text can't safely use jump labels — see
  below). All hand-validated via constructed `FuncCtx`/`func.validate()`
  cases, the truthiness branchless sequence additionally verified on
  real hardware (assembled+ran a Win64 .exe, confirmed exit code by hand
  computation). Full 454-test suite green after every commit (unaffected
  so far — `ssa_build.py` isn't wired into the compile path yet).

**Two RAW_ASM design gaps resolved this stretch** (both documented in
`docs/IR-DESIGN.md`, load-bearing for every future RAW_ASM site — list/
dict/set ops still ahead):
1. **Argument convention** (asked the user, didn't guess): a RawAsm's
   `args[i]` lands in the i-th register of a fixed `rax, rbx, rcx, rdx,
   ...` convention, matching every existing `_runtime_*` helper exactly.
   No new `Instr` field; the register allocator treats it like a `Call`
   with hardcoded-not-ABI-derived argument registers.
2. **No internal jump labels in RawAsm text** (caught by self-review
   before committing, not by a test failure): `target_text` is static
   per-call-site text with no fresh-label-minting mechanism, so two
   firings of the same RawAsm site in one function would emit duplicate
   NASM labels. Any RawAsm needing internal control flow must be written
   branchless, or promoted to real typed IR instructions (more blocks +
   ICMP/CONDBR) if it's too complex for that.

**Explicitly still not done**: general exception/try-except mechanism
(only hand-wired ZeroDivisionError exists), string indexing/`len()`/
methods, ALL list/dict/set/tuple operations, `TupleAssign`/`StarTarget`,
`Global`/`Nonlocal` (blocked on `.bss`/box-pointer addressing not yet in
the IR), f-strings, classes/methods/dunders, closures, generators, match
statements, `for` over non-range iterables. Expected to be mechanical
CALL/RAW_ASM wrapping per the design doc, lower risk per node type than
what's landed, but still substantial volume — string/list/dict ops in
particular will each need their own register-convention check against
the real `_runtime_*` helper before wrapping.

**After the wrapping pass**: linear-scan register allocator, X86_64Target
lowering, wire IR path into driver.py behind a flag, validate against full
454-test suite for x86-64 parity, only then ARM64Target lowering + port 73
runtime helpers to ARM64 + wire `--target linux-arm64` through WSL.

**Next step on resume**: pick the next wrapping target — string indexing/
`len()` or list literals/operations are the natural next units, both
bigger than what's landed so far. Was about to ask the user before
picking, given how much real design nuance the RAW_ASM sites have
surfaced this stretch (worth confirming pace/direction rather than just
plowing into list operations unprompted).

## Selfhost Debugging (paused, non-blocking)

Still segfaults compiling `test_simple.py` via the selfhosted binary — 7
distinct bugs found and fixed so far (Win64 shadow-space violations,
`@dataclass` default_factory codegen, shared-AST-node default-arg
collision, NULL truthiness checks, whole-program import merge ordering,
class-var inheritance gap, hardcoded-empty `__file__`). Full details are
in git history (`git log -p -- RESUME.md`) or the
`[[feedback-selfhost-debugging]]` memory for the gdb-first workflow and
exact fix locations. 8th bug not yet isolated. Resume only when asked;
not committed 2.0.0 scope.

## Other Pending (post-ARM64)
- macOS Intel x86_64 target (Mach-O, clang/ld) — independent of ARM64 IR.
- Garbage collector (refcounting) for x64 targets — independent of ARM64 IR.
- CODE_OF_CONDUCT.md, CONTRIBUTING.md, SECURITY.md, issue templates.
- Final 2.0.0 release pass: full test suite, CHANGELOG, version bump off
  `-preview`.
- (Deferred, only if user revisits) A `.csproj`-style project
  manifest/build-orchestration system — asked about once, acknowledged as
  a real but separate idea, no work started.
