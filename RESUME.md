# 2.0.0 Resume

## Directive
"Continue dev until we hit 2.0.0 ready," explicitly scoped 2026-06-18 as:
garbage collector, optimizations, selfhost-capable, ARM support, Mac
support (Intel + Apple Silicon), Raspberry Pi support (OS + bare metal).
**This is now running as an autonomous `/loop`** — see the user's `loop`
skill instructions for how to act without supervision (continue
established work, commit/push only on clear continuations, don't invent
new scope, stay reversible).

**Confirmed work order** (user answered explicitly when asked, given the
real dependency chain — full reasoning in `[[project-2.0-versioning]]`
memory):
1. Finish `ssa_build.py`'s mechanical wrapping pass (~40 node types left).
2. Linear-scan register allocator.
3. `X86_64Target` lowering; validate full parity vs the 454-test suite.
4. macOS Intel x86-64 target (reuses `target_linux.py`'s SysV/libc approach).
5. ARM64 lowering (Linux first).
6. macOS Apple Silicon (reuses step 5).
7. Raspberry Pi Linux (reuses step 5).
8. Raspberry Pi bare metal (needs a *freestanding* ARM64 target — new work).
9. Garbage collector (refcounting).
10. Optimization passes beyond the existing peephole dead-store pass.
11. Selfhost: resume the 8th not-yet-isolated bug (opportunistic, never blocking).
12. Release pass: CODE_OF_CONDUCT/CONTRIBUTING/SECURITY/issue templates, CHANGELOG, version bump off `-preview`.

Currently on **step 1**. Don't skip ahead to register allocator/lowering
work until the wrapping pass is substantially further along — that was
the user's explicit, twice-confirmed call (full IR rewrite over a smaller
alternative; full AST coverage before any end-to-end compile attempt).

**Tangential, NOT active work**: user is also building **uASM**, a
modular machine-code compiler with swappable backends/frontends, which
currently depends on asmpython (Python implementation, compiled by
asmpython, uses `import_binary` at runtime). Plan: finish 2.0.0 exactly
as scoped here first, fork asmpython into a uASM-facing Python frontend
*afterward*, as a separate effort. Zero impact on current IR/RAW_ASM
design decisions — don't let this influence anything below. See
`[[project-uasm-fork-plan]]` memory.

## Step 1 Progress: ssa_build.py Wrapping Pass

**Design**: `docs/IR-DESIGN.md`. SSA form, two value kinds (`Kind.INT`,
`Kind.FLOAT`), ICMP/FCMP are value-producing (sidesteps x86 `ucomisd` vs
ARM64 `fcmp` unordered-NaN differences at the IR level), linear-scan
register allocation, a `RAW_ASM` escape hatch for the 73 hand-written
`_runtime_*` helpers during migration.

**ARM64 toolchain** (resolved, working, not yet exercised by anything
real): WSL2 Ubuntu 24.04 (`wsl.exe -u root`) + `gcc-aarch64-linux-gnu` +
`qemu-user`. Smoke-tested with a hand-written `.s` file only — real use
starts at plan-step 5.

**Done and committed** (`ir.py`, `ir_builder.py`, `ssa_build.py`, latest
commit `550f532a`); working tree clean as of this write:

- `ir.py`/`ir_builder.py`: complete, stable data model + construction API.
- Primitive/control-flow core: literals (int/float/string), local
  read/write, **complete** primitive int/float arithmetic (`+ - * / // %
  ** & | ^ << >>`), unary ops, comparisons incl. chained, if/while/
  for(range)/break/continue, plain user-function calls, `BoolOp`,
  `IfExp`, `ExprStmt`, `Pass`, `AugAssign`, `MultiAssign`, `Del`.
- **String operations** (first real `RAW_ASM` sites): `str + str`, `str
  ==`/`!= str`, string truthiness (`if s:`, written branchless — see
  hazard note below), `len(s)`, `str(int)` (plain int only).
- **`ListLit`** (int/float elements only — first container type). Turned
  out *simpler* in the IR than codegen.py's version: no frame slot needed
  to park the header pointer across the two `malloc` calls, since an SSA
  `Value` just stays valid across later instructions by construction.
  Element writes are real typed `STORE`s; only the two `malloc`s are
  `RAW_ASM`.
- **`Subscript`** for `list[int|float]`: negative-index wraparound +
  bounds-check-raising-IndexError, both real typed-IR control flow
  (block diamonds + short-circuit branching), not RAW_ASM — this is
  genuine new logic, not a helper wrap.
- **Fixed a real latent bug**: both zero-division-check raise sites used
  `Op.CALL` to invoke `_runtime_raise`, but that helper reads `rax`/`rbx`
  by the fixed internal convention, not ABI-derived registers. Predated
  the RAW_ASM argument-convention resolution; never revisited until
  `Subscript`'s bounds-check needed the same `_runtime_raise` call and
  exposed it. Fixed via a shared `_build_runtime_raise` helper.

**Two RAW_ASM design rules established this stretch** (in
`docs/IR-DESIGN.md`, load-bearing for every remaining RAW_ASM site —
list/dict/set *operations*, not just literals, still ahead):
1. **Argument convention**: `args[i]` -> i-th register of `rax, rbx, rcx,
   rdx, ...`, matching every `_runtime_*` helper's existing convention.
2. **`target_text` keys are `(OS, ABI)` pairs**: `"win64"` /
   `"linux_x86_64"` / `"linux_arm64"`, NOT bare arch names — caught and
   fixed after `len(s)`'s `strlen` call exposed that Win64 and SysV pass
   args in different registers even on the same architecture. A
   `_X86_64_KEYS` constant keeps self-contained (OS-independent) sites
   consistent. **Don't reintroduce `"x86_64"` as a key.**
3. **No internal jump labels in RAW_ASM text** — `target_text` can't mint
   fresh per-instance labels, so two firings of the same site would
   collide. Write branchless (see string truthiness's `cmovz` +
   `lea reg, [rel $]` trick, hardware-verified correct) or promote to
   real typed IR blocks if a helper is too control-flow-heavy for that.

**Real assumption caught before becoming a bug**: `ListLit`'s first draft
copied `_build_binop`'s int->float promotion pattern. Checked sema first
— `ListLit` type-checking hard-rejects mixed-element-type lists, so no
promotion is ever needed there. General lesson: don't assume a promotion
pattern transfers between structurally-similar node types without
checking that node's actual sema rule.

**Explicitly still not done** (plan-step 1 remainder): general
exception/try-except (only hand-wired `ZeroDivisionError` exists), string
indexing/slicing/methods, list *operations* (append/index/slice/methods —
only the literal exists so far), ALL dict/set/tuple, `TupleAssign`/
`StarTarget`, `Global`/`Nonlocal` (blocked on `.bss`/box-pointer
addressing not yet in the IR), f-strings, classes/methods/dunders,
closures, generators, match statements, `for` over non-range iterables.

**Next step on resume**: continue the wrapping pass — list `append`/
methods, list-element assignment (`xs[i] = v`, the write side of the
Subscript work just landed), or dict literals are the natural next
units. Once the remaining surface is substantially covered, move to
plan-step 2 (register allocator).

## Selfhost Debugging (paused, non-blocking — plan-step 11)

Still segfaults compiling `test_simple.py` via the selfhosted binary — 7
distinct bugs found and fixed so far (Win64 shadow-space violations,
`@dataclass` default_factory codegen, shared-AST-node default-arg
collision, NULL truthiness checks, whole-program import merge ordering,
class-var inheritance gap, hardcoded-empty `__file__`). Full details in
git history (`git log -p -- RESUME.md`) or `[[feedback-selfhost-debugging]]`.
8th bug not yet isolated. Opportunistic only — never blocks plan steps 1-10.

## Other Notes
- macOS Intel and RPi/Mac ARM64 are plan-steps 4 and 6-8 above, not
  independent side work — sequencing matters, see `[[project-2.0-versioning]]`.
- (Deferred, only if user revisits) A `.csproj`-style project
  manifest/build-orchestration system for asmpython programs — asked
  about once, acknowledged as a real but separate idea, no work started.
