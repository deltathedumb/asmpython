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
commit `1458936b`):
- `ir.py`/`ir_builder.py`: complete, stable data model + construction API.
- `ssa_build.py`: int/float/string literals, local read/write, the
  **complete** primitive int/float arithmetic surface (`+ - * / // % **
  & | ^ << >>`, correct Python floor/zero-division/NaN semantics),
  unary ops, comparisons incl. chained (`a < b < c`), if/while/for(range)/
  break/continue, calls to plain user-defined functions.
- Three real bugs found by hand-tracing test cases (not guessing) — see
  `[[project-2.0-arm64-ir]]` memory for specifics: empty-else-block
  validation failure, missing terminator-guard for unreachable code after
  break/continue, missing zero-check on float `/`.

**In progress, NOT yet committed** (`ssa_build.py`, uncommitted as of this
pause): added `BoolOp` (short-circuit, Python value semantics — not
boolean-normalized), `IfExp` (both arms promoted to result type, merged via
phi), `ExprStmt`, `Pass`, `AugAssign` (reuses `_build_binop` via a synthetic
`BinOp`/`Name` pair rather than re-deriving the op dispatch). All four
primitive-only; non-primitive operands (str/list/dict/set/tuple/instance)
raise `SSABuildError` same as the existing arithmetic builders.

Caught and fixed one bug while writing `_build_boolop` before it ever hit a
test: the short-circuit block was created but never given a terminator
(`br` to the merge block), which would have failed `Function.validate()`'s
empty-block check exactly like the earlier `_build_while` bug. Fixed inline
— see the function's current state in the file.

**NOT YET VALIDATED**: an `Agent`/`Bash` python smoke-test for these five
new builders (constructing real `FuncCtx`s, building IR, calling
`func.validate()`) was queued but the tool call was rejected/interrupted
before running. **This is the immediate next step on resume** — run that
validation (or equivalent), fix anything it surfaces, then commit. Do not
assume the new code is correct un-exercised; the established pattern this
session is hand-verify every new builder against a real constructed case
before committing, not just eyeball it.

**Explicitly still not done**: general exception/try-except mechanism
(only hand-wired ZeroDivisionError exists), all string/list/dict/set/tuple
*operations* (methods, literals, indexing), f-strings, classes/methods/
dunders, closures, generators, match statements, `for` over non-range
iterables. Expected to be mechanical CALL/RAW_ASM wrapping per the design
doc (each already calls a `_runtime_*` helper today), lower risk per node
type than what's landed, but still substantial volume.

**After the wrapping pass**: linear-scan register allocator, X86_64Target
lowering, wire IR path into driver.py behind a flag, validate against full
454-test suite for x86-64 parity, only then ARM64Target lowering + port 73
runtime helpers to ARM64 + wire `--target linux-arm64` through WSL.

## Selfhost Debugging (paused, non-blocking)

Still segfaults compiling `test_simple.py` via the selfhosted binary — 7
distinct bugs found and fixed so far (Win64 shadow-space violations,
`@dataclass` default_factory codegen, shared-AST-node default-arg
collision, NULL truthiness checks, whole-program import merge ordering,
class-var inheritance gap, hardcoded-empty `__file__`). Full details
were in this file's previous version — see git history
(`git log -p -- RESUME.md`) or the `[[feedback-selfhost-debugging]]`
memory for the gdb-first workflow and exact fix locations. 8th bug not yet
isolated. Resume only when asked; not committed 2.0.0 scope.

## Other Pending (post-ARM64)
- macOS Intel x86_64 target (Mach-O, clang/ld) — independent of ARM64 IR.
- Garbage collector (refcounting) for x64 targets — independent of ARM64 IR.
- CODE_OF_CONDUCT.md, CONTRIBUTING.md, SECURITY.md, issue templates.
- Final 2.0.0 release pass: full test suite, CHANGELOG, version bump off
  `-preview`.
- (Deferred, only if user revisits) A `.csproj`-style project
  manifest/build-orchestration system — asked about once, acknowledged as
  a real but separate idea, no work started.
