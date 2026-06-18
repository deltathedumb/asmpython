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
12. Release pass: CODE_OF_CONDUCT/CONTRIBUTING/SECURITY/issue templates,
    CHANGELOG, version bump off `-preview`.

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
commit `c5b1ac73`); working tree clean as of this write:

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
- **`IndexAssign`** for `list[int|float]`: write side of list subscript;
  negative-index wraparound, no bounds check (matching codegen.py's
  existing silent-corrupt-on-oob behavior exactly).
- **`MethodCall` (str + list)**: all 33 str methods from `STR_METHOD_RUNTIME`
  (0/1/2-arg dispatch; special-cased `split`/`rsplit` `xor rcx, rcx`,
  `ljust`/`rjust`/`center` default-space and 2-arg `movzx rcx, byte [rcx]`
  fillchar extraction). List: `append` (int + float via `_float_to_int_bits`
  bitcast), `pop` (int + float via `_int_to_float_bits`), `extend`, `reverse`,
  `clear` (inline `STORE` to `LIST_LEN_OFF`—no helper), `sort` (int/str, no
  key/reverse yet), `insert`, `copy` (via `_runtime_list_slice` sentinels).
- **`for x in xs:`** over list/tuple: single-var only; buf reloaded each
  iteration to survive in-body `append` calls.
- **`len()`** extended: list/tuple and dict/set now resolve to a `LOAD` at
  offset 8; string path unchanged.
- **Container truthiness**: `list`/`tuple` and `dict`/`set` now supported in
  `_build_truthy_branch` via length-field `LOAD` + `ICMP != 0`.
- **`_float_to_int_bits` / `_int_to_float_bits`**: frame-slot bitcast helpers
  (store Kind.FLOAT, reload as Kind.INT and vice versa), matching codegen.py's
  `movq rax, xmm0` / `movq xmm0, rax` around list helper calls.
- **Fixed a real latent bug**: both zero-division-check raise sites used
  `Op.CALL` to invoke `_runtime_raise`, but that helper reads `rax`/`rbx`
  by the fixed internal convention, not ABI-derived registers. Predated
  the RAW_ASM argument-convention resolution; never revisited until
  `Subscript`'s bounds-check needed the same `_runtime_raise` call and
  exposed it. Fixed via a shared `_build_runtime_raise` helper.
- **`DictLit`**: allocs dict header via `_build_alloc_dict` (cap rounded up
  to next power of 2 ≥ 2n), calls `_runtime_dict_set` per k/v pair; float
  values bitcast via `_float_to_int_bits`; `**spread` entries call
  `_runtime_dict_update` in source order.
- **`SetLit`**: same dict-keyed-by-members layout with dummy value 1 (str
  elements only; int-element sets deferred until int→str helper lands in IR).
- **`TupleLit`**: heterogeneous `elem_types[]`, reuses list layout; cap
  rounded up to max(n,4).
- **`MethodCall` (dict + set)**: dict — `get` (with/without default via
  `_runtime_dict_get_default`), `keys`, `values`, `items`, `update`, `pop`,
  `contains`, `clear`; set — `add` (str), `clear`, `update`, `remove`,
  `discard` (CONDBR diamond: contains → pop only if present, no RAW_ASM
  branching). `dict.copy`/`setdefault` and set `union`/`intersection`/
  `difference` deferred.

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
indexing (`s[i]`) and slicing, `str.format()`, `TupleAssign`/`StarTarget`,
`Global`/`Nonlocal` (blocked on `.bss`/box-pointer addressing not yet in the
IR), f-strings, classes/instance methods/dunders, closures, generators, match
statements, `for` over dict/set/str/zip/enumerate/instance iterables,
`list.index()` and `list.count()` (need inline search loops),
`list.sort(key=...)`/`reverse=...`, `dict.copy()`/`setdefault()`,
`set.union`/`intersection`/`difference`, int-element sets.

**Next step on resume**: string subscript `s[i]` via `_runtime_str_char_at`,
then `for` over dict (key iteration via `_runtime_dict_keys` + list loop),
then `for` over str. After that: `TupleAssign`/`StarTarget` unpack, then
`list.index()`/`count()` inline loops. Once the remaining surface is
substantially covered, move to plan-step 2 (register allocator).

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
