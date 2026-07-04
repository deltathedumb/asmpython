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
12. **Stdlib completion** (added 2026-06-18): full implementations of all
    requested modules — see "Stdlib Status" section below.
13. Release pass: CODE_OF_CONDUCT/CONTRIBUTING/SECURITY/issue templates,
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
commit `0fd427e9`); working tree clean as of this write:

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
exception/try-except (only hand-wired `ZeroDivisionError` exists),
stepped string slices (`s[a:b:c]`), list slices (`xs[a:b]`),
`str.format()`, f-string segments with a format spec/conversion (`f"{x:
.2f}"`, `f"{x!r}"`) or bool/None/float/instance values, classes/instance
methods/dunders, closures, generators, match statements, `for` over
set/zip/enumerate/instance iterables, `enumerate()`/`zip()` as
standalone values (deferred — codegen only handles these inside for-loop
iteration), `list.sort(key=...)`/`reverse=...`, `dict.copy()`/
`setdefault()`, `set.union`/`intersection`/`difference`, int-element
sets, comprehensions with tuple-unpack targets/multiple `for` clauses/
non-list-or-tuple iterables, `isinstance()` with a tuple-of-classes or
class-name target (needs class-id tracking FuncCtx doesn't have yet),
nonlocal-box closures (Nonlocal itself is a no-op, matching codegen.py —
closures aren't supported there either).

**Done and committed** (latest commit `5e9ee118`):

- **String slicing** `s[a:b]` (no step) via `_runtime_str_slice`,
  self-contained helper, same text both OSes. Stepped slices and list
  slices still deferred.
- **List comprehensions** `[elt for var in iter (if cond)]` over a
  list/tuple — real IR loop (frame-slot loop-carried state, the
  established convention for loops in this file, not phi nodes/RAW_ASM)
  building the result via repeated `_runtime_list_append` calls.
  Extracted a shared `_build_list_append_raw` helper used by both this
  and the existing `list.append()` MethodCall builder. Tuple-unpack
  targets, multiple `for` clauses, and non-list/tuple iterables deferred.
- **`Global`/`Nonlocal`** statements (no-ops, matching codegen.py
  exactly) plus the actual blocker that mattered: a new `Op.GLOBAL_ADDR`
  IR op (modeled directly on `Op.STRING_ADDR`) wiring up global `Name`
  reads/writes via `LOAD`/`STORE` against the global's address, the same
  way a frame slot is read/written against `FRAME_BASE`. Found and fixed
  a real bug in the same pass: `_build_assign` unconditionally
  `alloc_slot`'d a fresh LOCAL for any target not already in
  `ctx.locals_`, which would have silently shadowed every `global x; x =
  value` with a same-named local instead of writing the actual global.
- **F-strings** (no format spec/conversion) — segments convert to str
  (str passes through, int via sprintf) and chain through
  `_runtime_str_concat`. Also wired into `print()`, matching
  codegen.py's behavior of printing each segment individually with no
  separator (not concatenating first). Extracted `_build_int_to_str`/
  `_build_str_concat` as shared helpers, now used by `str(int)`/`str+str`
  too instead of duplicating the RAW_ASM text.

- **`str` subscript `s[i]`** via `_runtime_str_char_at`; handles negative
  indices internally.
- **`for c in s:`** (str iteration) — strlen at entry, char-at per step.
- **`for k in d:`** (dict iteration) — walks `order_buf[0..len)`.
- **`TupleAssign`** all three forms: StarTarget (`a, *rest = xs`), single
  iterable unpack (`a, b = tup`), parallel swap (`a, b = b, a`).
- **`list.index()`**, **`list.count()`**, **`list.remove()`** — inline
  scan/shift loops with real IR blocks; no helper call.
- **`in`/`not in`** for dict/set (helper), list/tuple (inline scan).
- **`NamedExpr` (`:=`)** — walrus assign-and-return-value.
- **`dict |=`** and **`list +=`** in `AugAssign`.
- **`dict[k] = v`** (`IndexAssign` dict target).
- **All 8 remaining builtin wrappers landed**: `print()` (scalar args
  only — int/str/float/bool/None, real IR branching for bool/None
  dispatch, no f-strings/containers yet), `range(...)` as a value (not
  just a for-loop header — materializes a real `list[int]` via
  `_runtime_range_list`), `sorted()`/`reversed()` (copy-then-mutate-copy,
  reusing `_runtime_list_slice`'s INT64_MIN/MAX-sentinel whole-list
  trick), `sum()`/`any()`/`all()` (real IR loops with frame-slot loop-
  carried state, same shape as `_build_for_list` — not phi nodes, not
  RAW_ASM, since this is genuine control flow not a single helper
  call), `isinstance()` (single primitive-class target only, resolved
  statically at IR-build time — `bool` maps to runtime type `"int"`).
  Plus earlier: `int()`, `float()`, `bool()`, `abs()`, `hash()`,
  `max(a,b)`, `min(a,b)`, `list()`, `chr()`, `ord()`, `id()`.
- **Fixed a real import-time bug** found while validating `print()`:
  `_build_namedexpr` was defined AFTER `_EXPR_BUILDERS`'s dict literal
  but referenced inside it (landed in an earlier autonomous cycle) —
  `import ssa_build` raised `NameError` unconditionally. The 454-test
  suite never caught this since `ssa_build.py` isn't wired into the
  compile path yet. Fixed by moving the function above the dict,
  matching the file's established convention (every other builder is
  defined above the dict that registers it).

**Format-spec'd f-string segments — landed (2026-06-23), full 1:1 port**
(user explicitly chose "full port" over a common-cases-only scope when
asked, given this file has no automated parity test yet and getting it
wrong silently was the real risk): `_build_fstring_segment` now handles
everything `codegen.py`'s `_gen_fstring_segment` does (codegen.py:4018-
4142) — alignment (`<`/`>`/`^`, str/int/float), `.precision` truncation
for str, numeric `cfmt` translation (`d`/`x`/`X`/`o`/`f`/`e`/`g`, width
and zero-pad folded into the printf format string), `,`/`_` grouping
separators including the zero-pad+grouping combo (`f"{n:015,}"`), binary
format (`b`/`#b`), and `!r`/`!a` conversion (quote-wrapping via
`_runtime_fmt_elem`). The pure spec-parsing helpers (`_fmt_cfmt_for_spec`,
`_fmt_split_align`, etc.) are direct copies of `codegen.py`'s methods of
the same name minus `self` — none of the originals touch `self` either,
so this was genuinely mechanical. The runtime-call wrappers
(`_build_int_fmt`, `_build_float_fmt`, `_build_group_digits[_zeropad]`,
`_build_int_to_binary_str`, `_build_str_pad`, `_build_str_truncate`,
`_build_fmt_elem_quote`) are `RawAsm` wrappers around the same
pre-existing `_runtime_*` helpers `codegen.py` already calls. One
non-obvious trick worth remembering: a *plain* (no explicit `.Nf`/`d`/...
suffix) float-to-str still needs `codegen.py`'s `_emit_float_to_str`,
which has internal NaN/inf-detection branching on Windows — multiple jump
labels a single static `RawAsm` text blob can't safely contain (see
IR-DESIGN.md "RawAsm text must not contain internal jump labels"). Fixed
by routing through `_runtime_fmt_elem` (rbx kind=2) instead of calling the
target's `_emit_float_to_str` directly — `_runtime_fmt_elem` is a single
pre-existing internal label that already wraps that exact target-specific
logic once per program, so calling it sidesteps the per-RawAsm-site label
restriction entirely (`_build_float_to_str_via_fmt_elem`; the same trick
`_build_print_value`'s pre-existing float case already relied on). Scope
notes for later: instance segments (`__str__`/`__repr__` dispatch) and a
*plain* unspec'd bool/None segment (needs True/False/None static-string
selection) both stay deferred — orthogonal to format-spec parsing, not
touched by this pass. Verified with a standalone harness (no test runner
exercises `ssa_build.py` yet) covering ~20 spec shapes across str/int/
float/bool/None, with IR dumps spot-checked by hand for two of the more
involved cases (zero-pad+grouping, aligned float+cfmt) — both matched
expectations exactly. Full 454-test suite still green throughout (the one
known pre-existing failure, unrelated).

**Next step on resume**: stepped/list slices, then classes/instance
methods/dunders (the biggest remaining unit — method resolution, dunder
dispatch, instance layout). Once the remaining surface is substantially
covered, move to plan-step 2 (register allocator). Before starting
classes specifically, worth checking in on scope/pace given how much real
design nuance this file has accumulated.

## Stdlib Status (plan-step 12)

### From user's explicit list (commit `e9dd2545`)

- `abc` — Complete (abstractmethod/ABCMeta stubs; correct for compiled lang)
- `argparse` — Complete (506 lines; flags, positionals, mutex groups)
- `array` — **New**: typed array class (all CPython typecodes, list-backed)
- `base64` — Complete (b64/urlsafe/b16/b32 encode+decode)
- `binascii` — **New**: hexlify/unhexlify, b2a/a2b_base64, crc32, crc_hqx
- `collections` — Complete (deque, OrderedDict, defaultdict, Counter,
  namedtuple, ChainMap)
- `contextlib` — Complete (suppress, nullcontext, closing; ExitStack
  callbacks now actually called)
- `copy` — **Rewritten**: copy/deepcopy(list); copy_dict/copy_set added
- `csv` — Complete (reader/writer, DictReader/DictWriter, dialect)
- `errno` — **New**: POSIX error constants, errorcode dict, strerror()
- `functools` — **Rewritten**: partial class with `_fn` local-var call
  pattern; reduce, lru_cache, wraps, total_ordering
- `gc` — Complete (stub GC API; real refcounting is plan-step 9)
- `getopt` — **New**: full GNU getopt() and gnu_getopt()
- `inspect` — Complete for asmpython's needs (179 lines)
- `itertools` — Complete (chain, islice, product, combinations,
  permutations, groupby, pairwise, batched…)
- `json` — Complete (dumps/loads with indent; JSONEncoder/Decoder)
- `locale` — Complete (LC_* constants, setlocale, format_string, currency)
- `pickle` — **Rewritten**: int/str/float/list/dict; Pickler/Unpickler
- `queue` — **Rewritten**: task_done/join with unfinished counter;
  raises Empty/Full
- `signal` — Complete (POSIX constants, signal/getsignal, stubs)
- `stat` — **New**: S_IF*/S_I* mode constants, S_ISREG/DIR/LNK,
  filemode()
- `types` — **Improved**: SimpleNamespace (_set/_get/_del/_has/_repr/_eq);
  MappingProxyType
- `unittest` — **New**: TestCase (20+ asserts), TestSuite, TextTestRunner,
  FunctionTestCase, main()
- `urllib` — **Expanded**: urllib.request (HTTP GET/POST via socket),
  urllib.error; dotted `from urllib.request import urlopen` works
- `zipfile` — Complete (ZipFile read/write, ZipInfo, namelist)

### Notable constraints / differences from CPython

- `functools.lru_cache` / `cache` / `wraps` are pass-through stubs (no
  memoisation — requires a dict keyed by arbitrary argument tuples).
- `copy.copy` / `deepcopy` accept `list` parameters only in the generic
  form; use `copy_dict` / `deepcopy_dict` for dicts (asmpython's
  `isinstance(obj, list)` on an `object`-typed parameter has no runtime
  branch effect — dispatch must happen at the typed call site).
- `queue.join()` is a busy-spin in single-threaded programs; correct when
  used with `threading` (other threads call `task_done()`).
- `pickle` uses a text format incompatible with CPython's binary protocol.
- `unittest.main()` does not auto-discover test methods (no reflection);
  build `TestSuite` explicitly and call `TextTestRunner().run(suite)`.
- `urllib.request` supports HTTP only (no TLS/HTTPS — no ssl runtime).

### Missing entirely (deferred to step 12 continued)

`shelve`, `tarfile`, `zlib` (needs C), `ssl` (needs C), `xml`, `http.server`,
`smtplib`, `asyncio`, `concurrent.futures`, `multiprocessing`,
`sqlite3` (needs C), `codecs`, `token`/`tokenize`.

## Selfhost Debugging (paused, non-blocking — plan-step 11)

Still segfaults compiling real programs via the selfhosted binary — 7
distinct bugs found and fixed so far before this stretch (Win64
shadow-space violations, `@dataclass` default_factory codegen,
shared-AST-node default-arg collision, NULL truthiness checks,
whole-program import merge ordering, class-var inheritance gap,
hardcoded-empty `__file__`). Full details in git history (`git log -p
-- RESUME.md`) or `[[feedback-selfhost-debugging]]`. Opportunistic
only — never blocks plan steps 1-10.

**8th bug — FOUR real bugs found and fixed (2026-06-19)**: User
correctly pushed back on deferring this — a real codegen divergence
between the selfhosted binary and the Python-hosted compiler means
OTHER programs could hit the same bugs, not just the compiler's own
bootstrap path. This turned into a much deeper investigation than
expected: every fix attempt revealed the binary still crashed, but
with progressively narrower repros, eventually finding four distinct,
independently-real bugs. All four are fixed and committed; the
selfhosted binary rebuild with all four applied was the last step in
progress.

1. **`set |= other` corrupted the target pointer** (commit `7f5c6329`).
   `codegen.py`'s `AugAssign` handler only special-cased `ty == "dict"
   and stmt.op == "|"` for `_runtime_dict_update`. A set-typed target
   fell through to the generic int-arithmetic fallback, doing `or rax,
   rbx` on two ~40-byte dict/set header *pointers*. Fixed by widening
   to `ty in ("dict", "set")`. Real bug, triggered by `program.py:544`'s
   `targets |= sub`, but turned out NOT to be the actual crash cause
   (see #3) — just the first thing found while chasing it.
2. **`set <=`/`>=`/`<`/`>` fell back to raw pointer comparison**
   (commit `baab23bc`). No special case existed for set subset/
   superset comparisons in `_gen_compare`; they fell through to a
   plain `cmp rax, rbx` on the two set header pointers — a wrong-
   answer bug (not a crash) found while re-checking #1's trigger site.
   Added `_runtime_set_subset` and wired in all four operators.
   Hand-validated against real CPython semantics across a 3-set
   fixture (proper vs non-proper subset/superset, all 7 combinations
   correct).
3. **Closure free-variable type inference defaulted to "int" for
   unannotated container literals — the ACTUAL root cause** (commit
   `b7042fb0`, found by a dedicated investigation subagent after #1+#2
   turned out insufficient). `sema.py`'s `_prescan_fv_types` only
   inferred a captured free variable's type from an explicit `x: T =
   ...` annotation. `codegen.py:611`'s `GP_REGS = ("rax", "rbx", ...)`
   — an unannotated tuple literal captured by the peephole pass's
   nested `mov_dest_and_src` closure — fell through to a hardcoded
   `("int", None, None)` default. This made `dest not in GP_REGS` (a
   tuple-membership test) compile as a *dict*-membership test
   (`_gen_dict_in`/`_runtime_dict_contains`) instead of a list/tuple
   linear scan, misreading a 24-byte `LIST_HEADER` as a 40-byte
   `DICT_HEADER`. Confirmed via gdb: the misread header showed
   `cap=len=26`, exactly `GP_REGS`' element count. This explains the
   "crashes on almost ANY input" breadth precisely — the peephole pass
   runs on every compile. Fixed by adding a literal-shape fallback
   (tuple/list/dict/set/str/int/float) for unannotated locals.
4. **`_runtime_dict_items` push/pop landed `cap` in malloc's shadow
   space** (commit `8e8e74ec`, the actual remaining-crash culprit
   after #3 — found via a conditional gdb breakpoint on `malloc` with
   `$rcx > 0x100000`, which caught a corrupted size argument). Unlike
   every other `_runtime_dict_*` helper (which all use frame slots
   around calls), this one parked `cap` via `push rbx; sub rsp, 8`
   around the list-header malloc call — placing the saved value only 8
   bytes below the call's `rsp`, squarely inside the 32-byte Win64
   shadow space `malloc`'s own prologue is allowed to overwrite.
   Fixed by parking `cap` in a frame slot (`[rbp-72]`, grew the frame
   96→112 bytes) instead, matching every sibling helper's style.

**Verified end to end**: the original crash repro (`s = "hello";
print(s.upper()); d = {"a": 1, "b": 2}; print(d.items())`, the
smallest case found that triggered bug #4) now runs to completion with
no crash via the Python-hosted compiler. Full 454-test suite green
throughout all four fixes (the one new test, see below, fails on a
fifth, separate, NON-crashing bug — not a regression).

A side effect of this investigation: while validating, wrote
`tests/cases/999_comprehensive_codegen.py` (commit `cead00bb`) — a
single large test file whose `# expect:` block is captured by actually
running the file under real CPython, not hand-transcribed (the whole
point: a hand-written expected-output block can silently encode the
same wrong answer a human wouldn't catch either, exactly like bug #2's
wrong-but-plausible boolean). This test is what surfaced bug #4's
crash in the first place, and remains the easiest way to broadly
re-check codegen correctness going forward.

**Known remaining gap, NOT a crash, scoped out of this session**:
`print(d.items())` / `sorted(d.items())` print raw pointer integers
instead of `[('a', 1), ('b', 2), ...]`. Root cause identified:
`_runtime_list_repr`'s element-kind encoding (`codegen.py`'s
`_composite_repr_kind`/`_value_repr_kind`, `_REPR_KIND = {"str": 1,
"float": 2}`) has no kind code for `"tuple"` elements at all — only
int/str/float/list/dict. `_list_repr_kind` correctly reads
`list_el_type == "tuple"` from sema's `dict.items()` annotation, but
`_value_repr_kind` doesn't recognize `"tuple"` and silently defaults to
`0` (int). Fixing this properly needs `_runtime_list_repr`/
`_runtime_fmt_elem` to gain an actual tuple-element repr path (format
each slot per the dict's value type, not just print raw bits) — a real
feature addition, not a one-line fix. Left for a future session;
`999_comprehensive_codegen.py` will keep failing on this specific
section until it's done, which is intentional (a visible marker, not
silently skipped).

**Rebuild #1 result (build/asmpython_v11.exe, bugs #1-4 applied)**: real
progress — `test_min2.py`/a no-import single-print file went from
crashing 100% of the time to compile-and-exit-0 clean. But two things
remained: `build/my.py` hit a new, non-crashing `undefined variable
sys` compile error (not yet investigated — separate from the crash
chain), and a separate zero-import `print(...)` file STILL crashed:
gdb showed `strlen(NULL)` inside `_runtime_str_concat`, called from
`_resolve_tool` (`driver.py`) building `f"--{name} {override}"` where
`override: Path | None = None` — `Path.__str__` got called with
`self=NULL` because neither `_gen_fstring_segment` nor
`_emit_print_value`'s `instance:` dispatch branch had a runtime NULL
guard (only a STATIC "this expression is always None" check existed,
which can't catch a genuinely-Optional value that's None at one call
site and a real instance at another). **Fixed as bug #5** (commit
`052014fa`): added a `test rax, rax / jz` guard before the dunder call
in both places, routing None to the shared `_runtime_none_str`
constant. Hand-validated via a direct repro matching the exact
`_resolve_tool` shape (a function taking `override: Path | None`,
building the same f-string) — works correctly via the Python-hosted
compiler (`--nasm None` printed, no crash).

**Rebuild #2 result (build/asmpython_v12.exe, bugs #1-5 applied)**:
`test_min2.py` STILL CRASHES — same `_runtime_str_concat` /
`strlen(NULL)` signature, same `_resolve_tool` trigger
(`f"--{name} {override}"`), but now the crash happens AFTER bug #5's
fix runs (gdb backtrace points at `_resolve_tool.Lfstr_inst_end_441`,
literally inside the label my own fix added), with `rax` holding the
literal text `"--nasm "` going into the next concat. This means bug #5's
fix is correct in isolation (verified again via a close repro of the
real `_resolve_tool` shape — works via the Python-hosted compiler,
prints `--nasm None` exactly as expected) but the SELFHOSTED compile of
this exact code still does something different. **This is the same
selfhost-vs-Python-hosted divergence pattern as the very first repro
chased at the start of this session** (`Path.resolve()`/`with_suffix()`
working via Python-hosted but crashing via selfhost) — not a logic bug
in the fix itself, but something about how the SELFHOSTED BINARY's own
compilation of this exact branch-and-merge code differs from CPython's.
Bug #6, genuinely still open.

**Bug #6 — ROOT-CAUSED AND FIXED (2026-06-20)**: the actual cause was
in `asmpython/stdlib/argparse.py`'s `_Arg._convert`: `if self.type ==
Path: return Path(value)` ran unconditionally, even when `value` is
the unset-flag sentinel `None` — wrapping `None` in a real (but
broken) `Path` instance whose own `.p` field is `None`, instead of
leaving the argument as `None`. `_resolve_tool`'s `override: Path |
None` parameter (filled from `args.nasm`/`args.gcc`) got this treatment
on *every* compile, not just ones passing `--nasm`/`--gcc` — explaining
why the crash was so broad. Fixed (commit `d4c8a6ac`) by returning
`None` immediately when `_convert`'s input is `None`.

Three more real, distinct bugs were found and fixed clearing the path
to a full rebuild-and-verify cycle:

- **`_gen_boolop`/`_gen_truthy_test` tested `and`/`or` truthiness via
  raw `test rax, rax`** (pointer-nonzero), wrong for empty containers
  (`bool([])` must be `False`, but `[]` is a valid non-NULL pointer)
  and floats (value lives in xmm0, never checked). This crashed the
  selfhosted compiler on **every function with ≥1 parameter** —
  `parser.py`'s `_parse_param` does `defaults and defaults[-1] is not
  None`, true even when `defaults == []`. Commit `b898694f`. Also fixed
  a separate bug found while fixing this: `_collect_locals`'s closure
  free-var type inference doesn't track program.py's same-name-closure
  dedup rename, so the renamed half of two nested `flatten` functions
  (introduced by this same fix, in two different methods) silently lost
  its captured types. Sidestepped by naming the closures differently.
- **`driver.py`'s `_run()` passed `cmd: list[str]` to
  `subprocess.run()`**, but asmpython's `subprocess` stub only accepts
  a single string (it shells out via `os.system`). Fixed by joining
  `cmd` into a quoted string before the call (commit `d4c8a6ac`, same
  commit as the argparse fix — found together while verifying the
  rebuild). A related crash surfaced fixing this: `os.environ["PATH"] =
  ...` (bare `os.environ`, no `.copy()`) evaluates through the
  opaque-attribute stub (a NULL pointer) since only `.get()` was
  special-cased — writing through it as a dict header NULL-deref'd.
  Fixed properly at the type level (commit `7976fd1b`): sema now types
  `os.environ` as `"dict"`, and `_gen_attr` lazily allocates a real,
  persistent empty dict (cached in a new `_environ_dict` .bss slot) the
  first time it's touched any way other than `.get()`. Note `.get()`
  (→ real `getenv`) and subscript-assign (→ the lazy dict) are two
  separate, intentionally-disconnected stores — not a regression, just
  newly visible now that `os.environ` has a real type.
- **`_runtime_dict_update` dereferenced its `src` argument
  unconditionally** — crashed when `src` is the NULL stub from an
  unresolved attribute (`os.environ.copy()` before the fix above).
  Guarded with a NULL check (commit `67293344`).
- **`set.update(some_list)` corrupted memory**: real Python's
  `set.update()` accepts any iterable, but `codegen.py` always called
  `_runtime_dict_update`, which assumes its source is dict/set-shaped
  (reads `order_buf`/`buf` at dict-header offsets — a `LIST_HEADER` has
  neither, so it read adjacent heap memory as if it were those fields).
  Crashed compiling `tests/cases/03_fib.py` via
  `codegen.py`'s own `_collect_frame_bound`'s `acc.update(s.targets)`
  (`s.targets` is `list[str]`). Fixed (commit `195f8ef4`) by walking
  list/tuple arguments element-by-element via `_runtime_dict_set`
  instead of the bulk merge helper when the argument isn't dict/set.

**Verified end to end**: `build/asmpython_v18.exe`/`v19.exe` (rebuilt
selfhosted binaries with all of the above) compile, assemble, link, and
run `print("hello")` correctly with **zero crashes** — confirmed via
PowerShell with `ASMPYTHON_NASM`/`ASMPYTHON_GCC` env vars pointing at
no-space toolchain paths (`C:\Program Files\NASM\...` has a space,
which `_resolve_tool`'s `Path.is_file()` doesn't handle — separate,
un-investigated gap; use space-free copies for selfhost testing). Bug
\#6, as originally reported, is closed.

**Bug #7 — ROOT-CAUSED AND FIXED (2026-06-20, commit `a6c9a34b`)**:
crashed the selfhosted binary on every call to a user function taking
≥1 argument (`strlen()` called with a literal small integer like `8`,
not a real string pointer). Found by byte-diffing `_load_call_
operands`'s compiled output between the Python-hosted and selfhosted
compilers (identical — ruled out a codegen-divergence bug) and then
verifying every runtime VALUE through gdb (`offs`, `MS_ARG_REGS`, the
function's own code — all correct, ruling out data corruption). The
actual bug: `reg_loads: list = []` then `reg_loads.append((reg,
is_xmm, off))` — sema has no mechanism to infer a tuple-shape for a
list that starts empty and gets tuples appended later (only a list
*literal* whose elements are tuple literals gets that treatment).
`for reg, is_xmm, off in reg_loads:` therefore bound `off` to `"any"`
instead of `"int"`, and `_gen_fstring_segment`'s int→str conversion
only triggers for an exact `"int"`/`"float"`/`"instance:*"` match —
for `"any"` it silently no-ops, leaving the raw frame-slot-offset
integer to be fed directly into `_runtime_str_concat` as if already a
string. Fixed by replacing the tuple-list with three parallel,
homogeneously-typed lists (`reg_loads_reg: list[str]`, `reg_loads_
is_xmm: list`, `reg_loads_off: list[int]`), which sema types correctly.

**Bug #8 — ROOT-CAUSED AND FIXED (2026-06-20, commit `a6ab0abb`)**:
surfaced immediately after bug #7's fix cleared the way to it — every
function with more than a trivial one-line body crashed inside
`_runtime_dict_lookup_slot` (called from `_runtime_dict_contains` /
`_collect_locals`). Same bug *class* as bug #7 (an opaque/`"any"`-
typed value treated as a different runtime shape than it actually is):
`_collect_locals` built `nonlocal_set: set = set(getattr(f,
"nonlocal_vars", []))`. A `getattr()` call result is opaque (`"any"`)
to sema, and `_gen_set_call`'s `if at in ("set", "dict", "any"): hand
it straight back` branch assumes any `"any"`-typed `set(...)` argument
is *already* dict-backed — but `nonlocal_vars`'s actual runtime value
is a `LIST_HEADER` (24 bytes: cap/len/buf), not a dict (40 bytes,
different field layout). Iterating/membership-testing it as a dict
read whatever memory happened to follow the list's allocation as if
it were `buf`/`order_buf` fields. Fixed by using a plain `list`
instead of `set()` — `nonlocal_vars` is already deduplicated by the
pass that produces it, so list semantics work identically without
needing the broken `set()`-constructor path at all.

**Verified end to end (commit-by-commit, all three fixes applied,
`build/asmpython_v22.exe`)**: `print("hello")` (bug #6), a 2-argument
function call `add(2, 3) -> 5` (bugs #7 + #8), both compile, link, and
run correctly with the selfhosted binary — zero crashes. Full 454-test
suite green throughout.

**Bug #9 — multiple real fixes landed, still open (compile-time, not a
crash)**: `tests/cases/03_fib.py` (recursion + `for i in range(10):` +
`print(fib(i))`) still fails to compile under the selfhosted binary.
This turned into a chain of real, distinct bugs, all sharing one root
cause: **`getattr(obj, "field", default)` always types its result
`"any"` (opaque) to sema, regardless of `field`'s real declared type**
— and several codegen operations have no safe fallback for an
`"any"`-typed value:

1. **Unannotated class-var dict/list value-kind inference** (commit
   `60ba9aa6`). `codegen.py`'s own `SETCC = {"==": "sete", ...}`
   (unannotated) always recorded its value-kind as unset, defaulting to
   `"int"`. Fixed by computing the literal's homogeneous value/element
   kind directly from its own elements during `_collect_field_types`
   (which runs before `_check_expr` has populated `DictLit.value_type`/
   `ListLit.el_type`).
2. **`len()`/`set()` on `getattr()` results** (commit `711417a6`).
   `len()`'s codegen has no `"any"` fallback other than `strlen()` (the
   same gap `set()` has, already fixed once for `nonlocal_vars` as bug
   #8). Found and fixed **six** occurrences across `codegen.py`:
   `_cl_walk`'s `target_types`/`MethodCall.args`, `_gen_comprehension`'s
   and `_cl_walk_expr`'s `extra_for_*` fields, `_gen_closurebind`'s
   `nonlocal_vars` (a second instance beyond the one already fixed for
   bug #8), and a closure free-var count check in `_gen_call`. All were
   genuine bugs (confirmed by checking each field really is a real,
   always-present dataclass field, so `getattr()`'s defensiveness was
   unnecessary) but **none of them turned out to be 03_fib.py's actual
   crash mechanism** — fixing all six didn't change the error.
3. **`_gen_for`'s `orelse` + `_gen_constructor`'s `class_vars`**
   (commit `4c01065f`). Same pattern, two more occurrences. The
   `class_vars` one looked very promising: `_gen_constructor`'s
   `@dataclass`-style synthesis branch is the single code path *every*
   `@dataclass`-style constructor call goes through, including the
   **parser's own AST node construction** (`A.For(...)` etc.) — so a
   bug there could plausibly explain corrupted AST nodes. This changed
   the error from "list index out of range" to **"KeyError: key not in
   dict"** (a different failure, so SOME real corruption was fixed) but
   `03_fib.py` still doesn't compile.
4. **`cls_def = None` then reassigned in a loop** (commit `14d1feea`).
   Investigating fix #3 further: `class_vars = cls_def.class_vars if
   cls_def else []` was STILL `"any"`-typed even after switching from
   `getattr()` to direct field access, because `cls_def` itself starts
   as `cls_def = None` and gets reassigned to a real `A.ClassDef`
   inside a loop — sema has no flow-sensitive narrowing through a
   `None`-typed initial declaration, so the post-loop read stays opaque
   regardless of the reassignment. Deeper cause: `A.Module` (`self.mod`'s
   type) is an external/opaque class to sema (no introspection into
   `ast_nodes.py`'s dataclass field types), so `self.mod.classes`'s
   element type is unavoidably opaque too. Fixed by reading
   `class_vars` directly inside the search loop (both occurrences in
   `_gen_constructor`) instead of through a separate sentinel read
   after the loop — this is more correct regardless, but **did not
   change `03_fib.py`'s outcome**: still the exact same crash site
   (`Codegen.__gen_for`, `info.locals_[stmt.var]` → KeyError, same
   `Lendif_12959` label) after rebuild #4.

**Current status after 4 rebuild-and-verify cycles**: bugs #6/#7/#8
remain solidly fixed (`print("hello")` and a 2-argument function call
both compile/link/run correctly via the selfhosted binary — re-verified
after every one of bug #9's fix attempts, no regressions). `03_fib.py`
specifically still fails with `KeyError: key not in dict`, crash site
unchanged since fix #3: `Codegen___gen_for.Lendif_12959+57`, i.e.
`var_off = info.locals_[stmt.var]` (codegen.py ~line 2904) raising
because the `A.For` instance being compiled apparently doesn't have a
real `"var"` key in its backing dict. **Not yet conclusively
root-caused** — fix #3's `class_vars` theory was plausible (this is
exactly the code path that constructs `A.For` instances) but
empirically didn't fix it, so either: (a) there's a SEPARATE bug in
how `A.For` instances specifically get constructed (the parser site
that emits `A.For(var=..., ...)`, not `_gen_constructor` generically),
or (b) `_gen_constructor`'s `class_vars` fix is real but insufficient
— something else in that same function (the `fname, _fannot, fdefault
= cv` tuple-unpack on an opaque-but-list-shaped `class_vars` element,
not yet checked the same way the `getattr()`/`None`-sentinel angles
were) is still corrupting field names.

**Next steps on resume**: (1) check whether `A.For` specifically (as
opposed to other dataclass-constructed AST nodes) is built via a
DIFFERENT path than `_gen_constructor` — e.g. a hand-rolled
fast-construct helper the parser uses, which might have its own,
not-yet-found bug; (2) if it does go through `_gen_constructor`, set a
conditional gdb breakpoint catching the exact moment `A.For`'s `"var"`
key gets `_runtime_dict_set` (or fails to), to see definitively
whether the SOURCE field-name string itself is wrong at that point,
or whether the dict-set call never happens at all for that field; (3)
consider whether `cv`'s tuple-unpack (`fname, _fannot, fdefault = cv`,
where `cv` comes from iterating the now-correctly-list-typed but
still-opaque-element-typed `class_vars`) is itself miscompiled, same
bug class as bug #7's `(reg, is_xmm, off)` tuple-list issue — this
hasn't been checked yet and is a strong remaining candidate given the
pattern-match to a bug already found and fixed once this session.

**MAJOR FINDING (2026-06-21, supersedes the above as the active
lead)**: bug #9's true mechanism is much more fundamental than any
`getattr()`/tuple-unpack issue. Landed several more real, committed
fixes this session (`ea8885bc`, `25fe766c`, `76d4d3ac`, `14d1feea`,
`2ce1c7c1` — all the `_resolve_annot`/`base, el = annot` tuple-unpack
chain, root-caused via a minimal `d: dict = {}; d["a"] = 1` repro
that diverges between the Python-hosted and selfhosted compilers).
Those are real and worth keeping, but a *separate, much bigger* bug
was found while diagnosing why the dict-annotation fix still didn't
take effect: **user-defined method calls and constructor calls are
silently elided (no `call` instruction emitted at all) by the
selfhosted compiler**, independent of any annotation. Minimal repro
(`build/test_print_multiarg.py`): a class `Foo` with a method `bar`
that prints several things; `f = Foo(); f.bar()` at module level.
Compiled by `py -m asmpython`: all prints appear correctly. Compiled
by any selfhosted `asmpython_vNN.exe` (v32/v33 confirmed): only
module-level prints appear — `f = Foo()` compiles to `xor rax,rax`
(no malloc/dict_set at all) and `f.bar()` compiles to evaluating `f`
then discarding it (`_gen_method_call`'s final "unknown method on an
opaque receiver, return 0" stub) — `Foo__bar`'s compiled body exists
in the .asm but is never `call`ed anywhere.

Traced via gdb + manual dict-memory inspection (walking the order_buf
and slot buffer by hand — calling runtime helpers like
`_runtime_dict_get_default` directly from gdb's `call` segfaults,
possibly an ABI/shadow-space mismatch when invoked from the debugger,
so don't bother with that approach) at `Codegen.__init__`'s entry:
`self.mod.classes_sig` (the dict `_gen_call` checks via `if e.func in
self.mod.classes_sig:` to decide whether a call is a constructor) is
present in `mod`'s instance dict (key found at the right slot) but its
VALUE is an empty dict (cap=8, len=0), even though `mod.classes` (the
parser's own class list) correctly shows len=1 for the same compile.
Sema's `self.mod.classes_sig = self.classes` (sema.py ~line 2331,
near the end of `SemaAnalyzer.analyze()`) is supposed to populate it
from `self.classes: dict[str, ClassSig]` (built incrementally via
`self.classes[c.name] = sig` inside the class-signature-collection
loop, sema.py ~line 2135).

Extensive static re-reading of every step in this chain (the
`AttrAssign`/`IndexAssign`/chained-`Attr` codegen for
`self.classes[c.name] = sig`, `self.mod.classes_sig = self.classes`,
and the read side `self.mod.classes_sig` in `_gen_call`) did NOT find
a bug — every individual codegen step, read closely against the
actual generated .asm in `asmpython_v33.asm`, looks correct (key
strings match, dict_set/get_default argument registers match the
runtime helpers' calling convention, chained `self.mod.classes_sig`
correctly threads `rax` from the inner `self.mod` lookup into the
outer `.classes_sig` lookup without an intervening clobber). This
means the bug is either (a) in `self.classes[c.name] = sig`'s *value*
specifically — maybe `sig` itself, or the dict it's stored into, gets
corrupted by something upstream not yet checked, or (b) somewhere
genuinely not yet looked at in this chain.

Also discovered along the way: **print() statements added inside
`SemaAnalyzer` methods (`analyzed()`, `_check_stmt`, etc.) for
debugging never produced visible output when compiled into a
selfhosted binary**, even though the equivalent prints work fine
under `py -m asmpython`. This was initially mistaken for a
gdb-breakpoint-symbol-resolution issue (breakpoints on
`SemaAnalyzer___check_stmt`/`SemaAnalyzer___bind_name_from_value`
never hit, while `Codegen____init__`/`_runtime_dict_set` breakpoints
hit fine) but is now understood to be **the exact same root bug**:
since `SemaAnalyzer(mod, ...).analyze()` and all its `self.foo(...)`
calls are themselves user-defined method calls, and method calls are
the thing silently failing to emit `call` instructions, of course
debug prints inside those methods never ran, and of course gdb never
broke on those symbols — the methods are simply never invoked. This
was confirmed directly with the minimal `Foo.bar()` repro once it was
found, which doesn't depend on sema internals at all.

**Follow-up findings (same session, after the above)**:

1. **Bisected across existing selfhost generations**: `build/asmpython_v20.exe`
   and `v21.exe` both segfault outright trying to compile *any*
   class+method test file (a different, earlier-stage bug). `v22.exe`
   onward (the first version built right after bugs #7/#8 were fixed,
   *before* any bug #9 work started) all compile cleanly but exhibit
   the silent method-call elision. So this bug is not something the
   `_resolve_annot` work introduced this session — it's been present
   in every selfhost build since very early in this session, just
   never noticed because the only post-#7/#8 regression tests were
   `print("hello")` and a 2-arg *function* (not method) call.

2. **`Codegen.class_ids` (built fresh in `Codegen.__init__` via `for
   cls in mod.classes: self.class_ids[cls.name] = cid`) is correctly
   populated** (confirmed via gdb memory walk: cap=8, len=1 for a
   single-class test file) — this rules out "all `self.x[k]=v]` on a
   `self`-typed dict is broken" as too broad an explanation. The
   working case (`Codegen.__init__`, a short function, simple loop
   over a `list["ClassDef"]` param) and the broken case
   (`SemaAnalyzer.analyze()`, building `self.classes` from the same
   `mod.classes` list) look structurally identical in source but
   behave differently compiled.

3. **Found a second, code-PATH-narrower repro that fails at COMPILE
   TIME instead of silently**: a small class `Collector` with
   `self.classes: dict = {}` in `__init__`, a `collect(self, names:
   list)` method that loops `for n in names: sig = Sig(n);
   self.classes[n] = sig`, and a `count(self) -> int: return
   len(self.classes)` method. Under `py -m asmpython`: compiles and
   runs correctly (prints `3` then `True`). Under any selfhosted
   `asmpython_vNN.exe`: **compile-time** `KeyError: key not in dict`
   (not the earlier binaries' silent runtime no-op — a different,
   more specific failure for this exact shape). Traced via a gdb
   breakpoint+continue script logging every `_runtime_dict_get` key
   lookup (calling raw helpers from gdb's `call` mostly works for
   *this* one, just not `_runtime_dict_get_default` for some reason —
   inconsistent, be ready to fall back to a logging breakpoint
   `commands`/`continue` script if a direct `call` segfaults) up to
   the failing key: `"__lm_val_<id>"` — a list-method codegen temp
   slot name (`_collect_locals` only defines this for `list.count`/
   `list.remove`/`list.insert`, none of which this test program calls
   anywhere). The slot gets DEFINED under one `id(expr)` during
   `_collect_locals`'s AST pre-walk and LOOKED UP under a different
   `id(e)` during the real codegen walk for what should be the same
   AST node — i.e. `id()` (asmpython's own pointer-identity builtin,
   used here purely as a unique temp-name suffix, not by the *user*
   program) is returning different values for the same object across
   two different compiler passes. This is a strong, narrow, and very
   promising lead: if `id()`'s codegen — or, more likely, something
   about how `_collect_locals`'s walk vs. the real codegen walk visit
   AST nodes — doesn't guarantee revisiting *the same* Python/asmpython
   object both times, that would directly explain both this KeyError
   and quite possibly the broader method-elision symptom (if
   `_gen_method_call`'s own dispatch logic relies on matching against
   AST node identity or a `self.mod.classes_sig`-style lookup keyed
   by something that isn't stable across passes the same way).

**Next steps on resume**: (1) Pick up the `id()`-mismatch lead first —
it's the most concrete, reproducible, compile-time (not runtime-silent)
symptom found so far. Check `_collect_locals`'s entry point: does it
walk a *freshly re-parsed* copy of the function body, or the exact
same `FuncDef.body` list object the real codegen pass later iterates?
If self-compilation involves re-parsing or any AST transformation
between the two passes (e.g. a `match`-statement rewrite, a generator
transform, anything that calls `A.X(...)` to build replacement nodes)
for only *some* code paths, that would explain why `id(expr)` (from
the pre-pass) and `id(e)` (from codegen) diverge only sometimes. (2)
If that doesn't pan out, fall back to the original `self.classes`-
specific lead: add a print reachable from module-level code (not
inside any method, given the elision bug) to check `self.classes`'s
real length right after the collection loop in a selfhosted build,
or use the gdb memory-walk technique demonstrated above (walk
`self`'s order_buf/slot buffer by hand) to inspect it directly. (3)
Given the `KeyError` repro is compile-time and far more debuggable
than the silent-elision repro, prefer it for further investigation —
debug print()s placed inside the *user test program* being compiled
(not inside the compiler's own sema.py — those don't show output, see
above) should work fine and could help narrow exactly which
`_collect_locals` vs. codegen pass visits diverge.

**2026-06-21 continuation, post-summary**: landed several more real,
committed fixes chasing the nested-function nightmare (all confirmed
correct under `py -m asmpython`, full suite green throughout):
- `aa596ecf` — `set(fdef.params)`/`set(f.params)` in
  `Parser._find_free_vars`/`_propagate_transitive_free_vars` read
  `fdef.params` as opaque `"any"` (external-type attribute access),
  so `set()` handed the list back unchanged instead of iterating it —
  a function's own params weren't excluded from its computed
  `free_vars`.
- `77beeee2` — same bug class for `f.free_vars`/`f.nonlocal_vars`/
  `callee.free_vars`/`callee.nonlocal_vars` reads in the same function's
  fixed-point sibling-call loop.
- `02f5e470` — `by_name = {}` (unannotated dict literal in
  `_propagate_transitive_free_vars`) had its value kind default to
  `"int"` (sema can't see `A.FuncDef`'s real type), so
  `by_name.get(callee_name)` came back `"int"`-typed instead of
  opaque/instance, making `callee.free_vars` dispatch through the
  wrong codegen path. Fixed with an explicit `by_name: dict = {}`
  annotation.

After all three: `test_nested_def_minimal.py` (bare nested def, no
params) finally fully works end-to-end in a selfhosted binary. But
**`test_instance_type_repro.py` (a plain `Box` class, `__init__` +
one method, zero nested functions) has been broken in *every* build
since v37** — `Box()`/`box.get()` calls still silently elide (return
0), and `module.classes_sig` still reads back empty. This is
confirmed via a side-channel debug stash (`module.func_aliases`,
since `print()` inside any `SemaAnalyzer`/`Codegen` method never
surfaces output from a selfhosted binary — read directly from
`driver.py`, a plain function, after `sema_analyze()` returns) added
right inside the class-signature-collection loop in `analyze()`
(sema.py ~2226, `for c in self.mod.classes: ... self.classes[c.name]
= sig`): **`loop_ran` came back `"MISSING"` — the loop body never
executes a single iteration**, despite `module.classes` correctly
showing `len=1` with `"Box"` parsed. (Debug scaffolding has been
removed from `driver.py`/`sema.py` again — this paragraph is the
record of the finding, not a live diagnostic.)

This means the `classes_sig`-empty bug and the nested-function bugs
are two **separate, independent root causes** that happened to look
similar (both manifest as "user method/constructor calls silently
return 0"). The nested-function ones are now understood and fixed.
This one is NOT understood yet: `for c in self.mod.classes:` failing
to iterate even once, for a `module.classes` that's confirmed
non-empty in the same compile, needs fresh investigation — possibly
in `self.mod.classes`'s read itself (chained attribute access,
`self` → `self.mod` → `.classes`, the same shape that was the
original mid-session suspect) or in something that runs between
`module = load_program(...)` and this loop inside `analyze()`
(`_inject_assembly_class_if_needed`, the generator-transform loop,
the function-signature-collection loop, or the `@dataclass`
`__init__`-synthesis loop, all of which run first and could be
silently corrupting `self.mod`/`self.classes`/`self.mod.classes`
before this loop ever gets a chance to run). Next session: gdb-break
on `Codegen____init__` (reliable), walk `self`'s (the `Codegen`
instance) `"mod"` key to a `Module` instance, then walk *that*
instance's `"classes"` key directly (not `module.classes` from
Python — the actual runtime dict the selfhosted binary built) to see
if `mod.classes` itself is already corrupted by the time `Codegen`
sees it, vs. only `classes_sig` being wrong.

**Toolchain note for future selfhost testing sessions**: the `build/`
directory's many generated `.exe` files got externally wiped mid-session
(likely Windows Defender or similar quarantining freshly-built,
unsigned executables) — if `build/asmpython_v*.exe` binaries vanish
unexpectedly, that's the likely cause; just rebuild. Also: avoid output
filenames containing "update"/"install"/"setup"/"patch" — Windows
flags them for UAC elevation, which hangs non-interactive runs.

**2026-06-23 session — `__main__.py` now passes sema cleanly; gen1
builds and runs; full generation-2 self-compile still blocked.**
Picking this up specifically because the CLI/package work earlier this
session (subparsers, `Path.cwd`/`iterdir`/`rglob`, `packages.py`) made
`__main__.py`'s own import closure (`pathlib`, `tarfile`, `urllib_request`,
`shutil`, `tempfile`, `packages.py`) much bigger, surfacing several real,
independent stdlib/sema bugs that had nothing to do with self-hosting —
fixed all of them, confirmed via `sema.analyze()` on the full merged
program returning zero errors (previously ~10):

- `urllib_request.py`: mixed `str`/`int` list literal in `_parse_url`'s
  return; `_http_request` assumed an OOP `socket.socket` class that
  doesn't exist (`socket.py` is FFI-bindings-only, a socket is a raw int
  fd) — rewrote to raw `_socket.socket/connect/send_all/recv/close`
  calls; `urlretrieve()` called `tempfile.mktemp()`, which didn't exist
  — added it; `urlretrieve()`'s `[filename, headers]` return was also a
  mixed-type list — changed to a tuple.
- `tarfile.py`: `import os as _os` defeated the `os.listdir`/`os.getcwd`
  inline-codegen special case in `sema.py`/`codegen.py`, which matches
  the literal bound name `"os"`, not whatever it's aliased to — removed
  the alias (`import os`, `_os.` → `os.` throughout). `_os.makedirs`/
  `_os.readlink` don't exist anywhere (`os.py` is a pure FFI-bindings
  registry, no precedent for mixing it with real Python source) — added
  a local `_makedirs()` helper using the existing `os.mkdir` binding,
  and stubbed `readlink` to `""` (no portable equivalent, and only
  reachable from the tar-*creation* path, not extraction). Also: a
  literal `is_tarfile = None` immediately followed by `def is_tarfile(...)`
  — vestigial dead code; deleting it fixed an "undefined variable `_os`"
  error inside that function specifically (the redundant module-level
  binding was somehow interfering with that one function's scope
  resolution — not investigated further since removing the dead line was
  the obviously-correct fix regardless).
- `packages.py`: `BINARY_EXTS` was a tuple; `str.join()` only accepts
  `list`/`any`/`int`, not `tuple`, so `', '.join(BINARY_EXTS)` failed
  sema — changed to a list. `versions = entry.get("versions") or {}` is
  `any`-typed (`.get()` is always opaque), so `sorted(versions)` defaulted
  its element type to `"int"` instead of `"str"`, breaking the later
  `.join()` — fixed with an explicit `versions: dict = ...` annotation
  (forces `_bind_name_from_value`'s override path).
- `pathlib.py`: added `Path.cwd()` / `.iterdir()` / `.rglob(pattern)`
  (the latter two via the existing `os.listdir` inline helper + `fnmatch`).
- `shutil.py`: `which()` was a complete stub (`if os.path.exists(name):
  return name; return ""` — never actually searched `PATH`). This one
  matters beyond `__main__.py`'s sema acceptance: `driver.py`'s own
  `_resolve_tool()` falls back to `shutil.which("nasm"/"gcc")`, and that
  silently worked under the Python-hosted compiler (real Python's
  `shutil`) but made a freshly-built gen1 binary unable to find its own
  toolchain. Implemented a real `$PATH`/`os.pathsep` search with
  Windows `.exe`/`.bat`/`.cmd` extension fallback.
- **Real, independent sema bug, not just a stdlib gap**: `d[k] = v` on a
  `dict`-typed (or bare, un-narrowed) local never updated
  `scope.dict_value_types`, only validated against it — so a dict
  declared `d: dict = {}` (or just `dict`, no `[K, V]`) stayed pinned to
  the unknown-sentinel (`"any"`) forever, even after every write agreed
  on a concrete value type. Every later `d.get(...)`/`d[k]`/`d.values()`
  read back that default, so e.g. a `dict[str, str]` built purely via
  subscript-assignment got its values printed as raw garbage pointers
  formatted as ints. Confirmed via a from-scratch repro (`d: dict = {};
  d["x"] = "1"; print(d.get("x", "missing"))` printed a 15-digit garbage
  number under *both* compilers, not just the selfhosted one — this is
  not a self-hosting bug). Fixed in `sema.py`'s `IndexAssign` handler:
  on the first concrete write to a dict whose tracked value type is
  still `"any"`/`"int"`, pin `scope.dict_value_types[name]` to the
  written type (mirrors the existing `list.append()`-on-`"?"` pinning
  rule). A mismatched *later* write (a genuinely heterogeneous dict,
  e.g. `pickle.py`'s `loads()` building a dict whose values vary by a
  parsed tag byte) now widens back to `"any"` instead of raising — the
  old hard "dict[k] = v: dict values are X, got Y" error only ever fired
  for non-`Name` targets before this session (nothing had ever pinned a
  `Name`-rooted dict's value type via a write before), so widening
  instead of erroring here doesn't mask any previously-enforced check.
  Full test suite still 454/455 (same pre-existing unrelated failure)
  after this change.

With all of the above fixed, `sema.analyze()` on the full `__main__.py`
whole-program merge returns **zero errors** for the first time. Rebuilt
gen1 successfully (`asmpython_gen1.exe`, ~4.97MB), and it works
correctly as a general-purpose compiler — verified compiling and running
several from-scratch test programs (`print()`, `isinstance()`
dispatch, dicts, the new dict-value-type fix's own repros). It also now
correctly locates `nasm`/`gcc` via the `shutil.which()` fix.

**Generation-2 (gen1 self-compiling `__main__.py`) still segfaults.**
Narrowed to a small, fast (no gen1 rebuild needed to iterate)
self-hosting-only repro, independent of `__main__.py`'s size:

```python
# pkgtest/__init__.py
from .mod_a import BINDINGS as _A_BINDINGS
REGISTRY: dict = {"a": _A_BINDINGS}
# pkgtest/mod_a.py
BINDINGS = {"x": 1}
# main file
from pkgtest import REGISTRY
def main() -> None:
    a: dict = REGISTRY.get("a") or {}
    print(len(a))
main()
```

Python-hosted compiles and runs this correctly (prints `1`). gen1
segfaults *compiling* it (i.e. gen1 itself crashes — this isn't a wrong-
output bug in code gen1 produces, like the isinstance one below). A
`from .mod_a import BINDINGS as _A_BINDINGS`-style relative *value*
import (resolved by `program.py`'s `_materialize_value_imports`) is
necessary to trigger it — removing the relative import (e.g. inlining
`BINDINGS` directly) makes it disappear. This exact mechanism is also
why `import os` alone (no usage at all, just the bare import statement)
reliably crashes gen1 while `import sys`/`math`/`random`/`gc`/`io`/`re`
don't: `os`'s `BINDINGS` reaches `STDLIB_BINDINGS` (in
`asmpython/stdlib/__init__.py`) via this exact `from .os import BINDINGS
as _OS_BINDINGS` pattern, baked into gen1's *own* data at gen1's *build*
time — confirmed this isn't about `os.py`'s content at all: truncating
its `BINDINGS` dict to 3 entries, then to `{}`, then deleting the whole
file down to a bare docstring, made no difference whatsoever to the
crash (expected in hindsight — gen1 already has this data baked in from
when *it* was built; editing the source file on disk afterward can't
touch it). Backslash-escaped string values (`os.py`'s `sep`/`linesep`/
`devnull` Windows variants) were a strong early suspect (a plausible
string-literal-escaping bug) but ruled out directly: stripping them
changed nothing, and a standalone `s: str = "\\"` compiles and runs
correctly under gen1.

gdb backtrace on the minimal repro above:

```text
strlen() [msvcrt]
  <- _runtime_str_concat
  <- str_8190                  (a codegen-synthesized helper, not user code)
  <- ?? (corrupted/unsymbolized frame)
  <- ??
  <- ??
  <- _runtime_dict_get_default
  <- itoa_str_buf
  <- ??
```

`itoa_str_buf` (int-to-string) feeding into `_runtime_dict_get_default`
feeding into a synthesized string-concat helper matches
`program.py`'s `_merge_import_bindings`'s nested `key(stmt)` dedup-key
builder almost exactly (`"from:" + str(stmt.level) + ":" + stmt.module +
":" + ",".join(stmt.names)` — `program.py:886`), which runs once per
`Import`/`FromImport` statement collected across every merged module via
`_collect_import_stmts`. Strong suspect: one of `stmt.module`/
`stmt.names` reads back corrupted (null or garbage pointer) specifically
for a *relative* (`from .x import y`) import statement when gen1 itself
walks it, feeding a bad pointer into `_runtime_str_concat`'s `strlen`.
Not yet confirmed at the single-line level — next session should gdb-
break on `key` directly (or instrument `_collect_import_stmts`'s caller)
and inspect `stmt.level`/`stmt.module`/`stmt.names` for the relative
import in the minimal repro above, the same way the `Box`-class
investigation above walked `self`'s dict by hand.

This is independent of the **previously-found, still-unfixed isinstance
bug** (separate investigation, same session): `isinstance(x, FirstClass)`
always returns false in code *gen1 produces* (not gen1 itself crashing)
when `FirstClass` is the first class declared in the program being
compiled — 2nd/3rd/etc. classes classify correctly. Reproduced with a
minimal `Foo`/`Bar` two-class `isinstance` dispatch test, compiled by
gen1 and run; confirmed positional-independence (swapping check order in
the `if`/`elif` chain doesn't change which class fails — it's about
declaration order, i.e. runtime class id 0, not source order of the
checks). `_gen_isinstance` (`codegen.py:13735`) correctly uses `-1` as
the no-class-tag sentinel (not `0`), so the bug isn't the obvious
"0 is falsy" trap; root cause not yet found.

**2026-06-23 session, continued — two real, confirmed, fixed bugs; the
relative-value-import crash above is resolved; `import os` survives but
is narrowed to one precise remaining cause.**

1. **Fixed: `program.py`'s `key()` (`_merge_import_bindings`, line ~886)
   had no `isinstance` narrowing for the `FromImport` case** — it was
   `if isinstance(stmt, A.Import): return "import:" + stmt.module` followed
   unconditionally by `return "from:" + str(stmt.level) + ...`, with no
   `elif isinstance(stmt, A.FromImport):` guard. Since `stmt`'s parameter
   has no type annotation, sema only narrows its type *inside* an explicit
   `isinstance` block (confirmed via gdb: `stmt.module` read inside the
   `if isinstance(stmt, A.Import):` arm correctly compiles to
   `_runtime_dict_get_default`, but `stmt.level`/`.module`/`.names` in the
   unnarrowed fallthrough arm all compiled to `_gen_attr`'s "unknown attr on
   opaque/int type" stub (`codegen.py` ~4224-4227: evaluate the object for
   side effects, then `xor rax, rax` — silently substituting 0/NULL).
   `",".join(stmt.names)` then got a NULL list pointer, corrupting memory
   and crashing somewhere downstream (`_runtime_str_concat`'s `strlen` on a
   garbage pointer) — this was the exact minimal repro from the previous
   entry (`from .mod_a import BINDINGS as _A_BINDINGS` /
   `REGISTRY: dict = {"a": _A_BINDINGS}`). **Fix**: added the missing
   `elif isinstance(stmt, A.FromImport):` arm (with a `return ""` fallback
   for neither case). Rebuilt gen1; the minimal repro and a bare,
   unused `import pkgtest` (no value-import at all) now compile and run
   correctly. No regressions (454/455, same pre-existing failure).

2. **Fixed: `_gen_isinstance` (`codegen.py:13735`) never null-checked the
   instance pointer before calling `_runtime_dict_get_default`** — `isin
   stance(x, Cls)` compiled to `gen_expr(x)` (rax = x, possibly 0/NULL if x
   is statically opaque and happens to be Python `None` at runtime) followed
   *unconditionally* by `_runtime_dict_get_default(rax, "__class__", -1)`.
   `dict_get_default` dereferences its first arg's capacity field with no
   NULL guard, so `isinstance(None, AnyClass)` segfaults instead of
   returning `False` whenever `x`'s static type can't rule out `None`
   (e.g. a value pulled from an untyped tuple/list element, or an optional
   field). Found via the **real, pre-existing, fully reproducible
   discovery this session that gen1 cannot compile *any* program using
   `@dataclass`** — confirmed even on the bundled test suite's own
   `tests/cases/205_dataclasses_module.py`. Bisected to the precise
   trigger: a class-level variable declared with a bare annotation and no
   default (`x: int`, not `x: int = 0`) — `@dataclass` always hits this
   since dataclass fields are normally bare-annotated. Sema's dataclass-
   `__init__`-synthesis / field-type-inference code does
   `isinstance(fvalue, A.Call)` to detect a `field(default_factory=...)`
   default; when there's no default, `fvalue` is `None`, and the
   unguarded isinstance crashed. **Fix**: `_gen_isinstance` now does
   `test rax, rax; jz <none-arm>` right after evaluating the instance,
   jumping straight to "no match" (`rax = 0`) instead of calling
   `dict_get_default` on a NULL pointer. Rebuilt gen1; every dataclass
   repro from this investigation (bare int field, multi-field, frozen,
   positional/keyword construction, declared-but-never-instantiated, the
   bundled `205_dataclasses_module.py` test) now compiles and runs
   correctly. This is a **different** bug from the "isinstance against
   first-declared-class always false" one in the entry above (that one is
   about *wrong output* in code gen1 *produces*; this one is gen1 *itself*
   crashing while compiling) — both remain independently worth keeping in
   mind, but only this one was root-caused and fixed this session. No
   regressions (454/455).

3. **`import os` still segfaults gen1, even after both fixes above —
   narrowed to one precise, well-isolated remaining cause.** Bisection
   ruled out, with direct A/B tests against gen1 (not just python-hosted):
   - **Not BINDINGS content** — truncating the real `os.py`'s `BINDINGS`
     to a single entry, then to `{}`, then to a totally empty file, all
     still crash identically (confirmed by editing the real bundled file
     with a backup/restore, since a project-local `build/os.py` does NOT
     shadow the bundled stdlib module of the same name — verified
     separately by injecting an unambiguous syntax error into a
     project-local `build/os.py` and observing zero effect on the build).
   - **Not the `Func`/`Const` dataclasses, not the relative `from . import
     Const, Func` class-import, not multi-submodule value-import
     aggregation** — a from-scratch package replicating
     `asmpython/stdlib/__init__.py`'s exact shape (two submodules each
     value-importing a `BINDINGS` dict into an aggregating `__init__.py`)
     compiles and runs fine.
   - **Not the file's content at all** — copying the *real, unmodified*
     `asmpython/stdlib/os.py` verbatim into a same-shaped local package
     under a different name (`realos_test.osfull`, a regular project
     import, not the bundled-stdlib name `os`) compiles and runs fine.
   - **Is specific to the bundled-stdlib resolution path for the literal
     name `os`.** `sema.py`'s `_load_module()` (line 478) is the relevant
     code: a generic `if key in STDLIB_BINDINGS: return STDLIB_BINDINGS
     [key]` against the registry `asmpython/stdlib/__init__.py` builds via
     `from .os import BINDINGS as _OS_BINDINGS` (and one such import per
     module: math, sys, time, random, socket, ...). Since `sema.py` itself
     is compiled into gen1, this whole registry is materialized as baked-
     in data *at gen1's own build time* — gen1 never re-reads `os.py`'s
     source when compiling a program that imports it, which is why
     content edits to the live file have zero effect on the crash.
     `math`/`sys`/`random`/`gc`/`io`/`re` all go through this exact same
     mechanism and work; only `os` crashes. Since the lookup code itself
     (`_load_module`) is generic with no per-module branching, the
     remaining suspect is that something specific to compiling
     `asmpython/stdlib/__init__.py`'s `from .os import BINDINGS as
     _OS_BINDINGS` statement *during gen1's own build* bakes in bad/
     corrupted data specifically for the `"os"` key (not a bug reachable
     by writing ordinary asmpython programs — only by self-hosting).
     Next session: instrument or gdb-trace `_materialize_value_imports`
     specifically while *building gen1 itself* (not while gen1 compiles
     something else) to see what gets baked in for `STDLIB_BINDINGS["os"]`
     vs `STDLIB_BINDINGS["math"]`; comparing the two side by side (e.g. via
     a temporary debug dump of the materialized AST before codegen) should
     show the divergence directly. `os.py`'s only attributes not shared by
     a working module like `time.py`/`random.py`: `arg_types=("str",
     "list_buf")` (the `_stat` binding's raw-buffer FFI arg kind, also used
     by `_gui_sdl.py`, untested) and the largest entry count (39) of any
     *currently-tested* stdlib module — neither confirmed as the cause.

**2026-06-24 session — root-caused and fixed the `copy.deepcopy`-on-AST-
nodes bug (commit `add2a5f8`); ran the user's new "ultimate test" (gen1
self-compiling `__main__.py` to produce gen2) and found it still fails,
narrowed to a precise, much smaller symptom than before.**

**Bug #10 — ROOT-CAUSED AND FIXED**: every observed symptom this session
(the `@dataclass`-with-defaults segfault, plain-function omitted-default
segfaults/errors, and `slice_a.py`'s for-loop segfault) traced to ONE
cause: `sema.py`'s `_bind_args` (fills a call's omitted-default argument
slots) called `copy.deepcopy()` on AST expression nodes to give each call
site a fresh node identity. Under gen0 (CPython), `import copy` resolves
to the real generic stdlib module and works on any object. Under
self-hosted gen1, the SAME `import copy` statement resolves to
asmpython's own bundled `stdlib/copy.py` instead — whose `deepcopy()` is
hard-typed to accept only `list` values and unconditionally reads
list-header offsets (cap/len/buf) out of whatever it's given. Handed an
AST node (not a list), it reads garbage through the wrong memory offsets,
corrupting the cloned default value — explaining the segfaults (garbage
treated as a pointer and dereferenced) and "list index out of range"
errors (garbage caught by a bounds check) alike.

**Fix**: removed the top-level `import copy` from `sema.py`; replaced the
single `copy.deepcopy(fixed_defaults[i])` call in `_bind_args` with a new
`_clone_default_expr` method that explicitly reconstructs fresh
`IntLit`/`FloatLit`/`StrLit`/`ListLit`/`DictLit` nodes (the only types
`parser.py`'s literal-default-value check permits), recursing into nested
list/dict elements, with a same-reference fallback for any unexpected
type. 454/455 tests pass (same pre-existing failure), no regressions.

**Verified against a rebuilt gen1** (re-running the original repro
scripts): `dctest.py`/`dc_b.py`/`dc_f.py`/`dc_h.py` (the
`@dataclass`/plain-function omitted-default repros) now all correctly
compile and run, matching gen0 exactly — confirms this was the real fix
for that whole symptom family.

**Two residual, DISTINCT failures remain post-fix** (not yet root-caused):

1. `dc_g.py` (a call passing all-keyword arguments out of declared
   order) still fails to compile: `asmpython: list index out of range`.
2. `slice_a.py` (negative-index list slicing inside a `for` loop) still
   segfaults at runtime. Re-diffed gen0-vs-gen1 `--emit-asm` output for
   this file against the NEWLY rebuilt gen1 — byte-for-byte IDENTICAL
   spurious-instruction divergence as before the fix (`_gen_for_list`
   taking its `unpack` branch when it shouldn't, via `unpack =
   bool(stmt.targets)`). Traced `stmt.targets`' construction: `parser.py`'s
   `_parse_for` (~line 2031) computes `multi = [] if single else targets`
   then ALWAYS explicitly passes `targets=multi` at both `A.For(...)`
   call sites (~lines 2064, 2075) — never omitted/defaulted, which rules
   out the just-fixed `_bind_args`/`_clone_default_expr` path entirely
   for this specific bug. Built two closer repros to isolate it
   (a bare ternary-list-vs-variable assignment; the same ternary result
   passed through a `@dataclass` constructor's keyword arg and read back
   via attribute) — **both compiled and ran correctly** under gen1,
   failing to reproduce the bug in isolation. So bug #10's fix is
   confirmed NOT to touch this; the real mechanism is still open and
   needs investigation closer to the actual `_parse_for`/`_gen_for_list`
   shapes (not yet isolated — next session should try reproducing with
   the exact `multi = [] if single else targets` ternary feeding
   `A.For(...)`'s `targets=` kwarg specifically, since the minimal
   repros so far used a hand-rolled dataclass, not `A.For` itself).

**"Ultimate test" run (the user's explicit framing): gen1 compiling
`asmpython/__main__.py` to produce gen2.** Built cleanly via gen0 to
confirm gen1 itself is healthy (5.18MB exe), then had gen1 compile the
same `asmpython/__main__.py` entry point gen0 used to build it.
**Result: a new, more fundamental bug.** gen1 produced
`asmpython_gen2.asm` at only **4,426 lines**, versus **~510,000+ lines**
when gen0 compiles the identical source — with **zero errors/warnings**
emitted during the compile itself (silent truncation, not a crash). The
truncated output fails at the assemble step: `error: symbol
'userfn_main' not defined` (a `call userfn_main` with no matching
function-label definition anywhere in the file; `grep -c "^userfn_"`
on the gen2.asm found **zero** function labels at all). This strongly
suggests gen1's import/module-merging logic is silently failing to
inline the transitive closure of `__main__.py`'s imports (parser/sema/
codegen/etc., the entire compiler) the way gen0's whole-program merge
does — NOT yet root-caused. Next session: instrument or bisect
`program.py`'s module-merge entry point (the same `_materialize_value_
imports`/`_merge_import_bindings`/`_collect_import_stmts` machinery
implicated in earlier `import os` investigations above) specifically
while gen1 (not gen0) processes a multi-file project, to see where the
closure stops expanding — likely the next concrete, high-value thread
given how small and reproducible the symptom is (a fixed, deterministic
4,426-line output with no error, easy to re-run against any incremental
fix attempt without needing a full gen1 rebuild each time).

**Merge from `origin/beta` (commit `48d2ded8`)**: a concurrent agent's
work landed three new commits upstream (general-decorator desugaring,
dotted-name default args, `=None` default-param mistyping) while this
investigation was in progress. Conflicts in `parser.py` (two genuinely
independent `Parser.__init__` features added by each side — kept both)
and `sema.py` (comment/style-only, no logic divergence) were resolved
and merged cleanly; 454/455 tests still pass post-merge.

**2026-06-25 through 2026-07-02 sessions — isinstance/type-pollution bug
family, six more real fixes.** Continuing the gen1-self-compiling-
`__main__.py` thread. Root cause discovered: **any `isinstance(x, (T1,
T2))` (multi-type tuple form) crashes gen1** (SIGSEGV) regardless of
whether it matches — single-type `isinstance(x, T)` is fine. Every
multi-type isinstance across `sema.py`/`codegen.py`/`parser.py`/
`program.py`/`ir_lower.py` was split into separate single-type checks
(with explicit narrowing casts where the body needs type-specific
fields, e.g. `_sw: A.While = s`) to work around it — this needs to stay
the default going forward, not just a one-time cleanup.

A second, related root bug: `isinstance(targets[0], str)` on a list
element is **statically resolved by gen1 from the list's inferred
element type** ("int" by default), not evaluated at runtime — silently
always `False`. This broke `_parse_for_target`'s single-vs-multi-target
detection (`var = ""` → `KeyError` in `_gen_for`); fixed (`b07b92c5`) by
making `_parse_for_target` always return a list and switching every
caller to `len(targets) == 1` instead.

Also found and fixed, all the same "opaque/unannotated attribute
defaults to int, breaking whatever gets called on it next" bug class:
`stdlib.os.BINDINGS["getenv"]` read through a module-attribute chain
evaluating to null (module attribute chains compile to `xor rax, rax`
per `stdlib/__init__.py`'s own documented limitation) — fixed
(`d74621fb`) via `self.imported_modules["os"]["getenv"]` instead; three
`sema.py` sites using a bare `m.params`/`m.body` (opaque `A.FuncDef`)
directly inside `enumerate()`/slicing/`len()`, segfaulting on any
bundled stdlib class with methods — fixed with explicit `: list`
casts (`8f796682`); four more instances of the same pattern in
`sema.py`/`parser.py` — a class-name cast, a tuple-arity `len()`, a
`set()` local, and a `Parser(...)` constructor call needing an
explicit `: Parser` cast so `._check()`/`._eat()` resolve (`89fe9366`).
Six codegen.py corruption points from stray edits (garbage strings
replacing real function bodies) were also found and restored
(`b07b92c5`).

**2026-07-03/04 session — root-caused and fixed the actual driver.py
parse crash blocking gen2; started the user-requested x86-64-backend
migration for gen1 and found (then fixed) three more real, independent
bugs before hitting the next blocker.**

**Bug #11 — ROOT-CAUSED AND FIXED**: gen1 segfaulted specifically while
parsing `driver.py` during the gen2 whole-program BFS, after 54+ other
modules parsed cleanly. Bisected `driver.py` (initial suspects: CRLF
line endings, then the file's one bytes literal, `b'\x89PNG\r\n\x1a\n'`)
down to a single line, then to a minimal, fully standalone repro that
reproduces even via a fresh gen0 build (a real miscompilation bug
reachable from ordinary source, not specific to gen1's own self-compiled
state): `x = b'\x89\x89'` alone segfaults; a single `\x89`, or two
non-hex fallback escapes (`\z\q`), do not. Root cause: `parser.py`'s
`_parse_primary` BYTES-literal branch did `for c in t.value` where
`Token.value` is declared `object` (opaque) on the dataclass — sema
can't see through the read, so the iteration used the wrong codegen
path, reading heap garbage depending on the resulting string's memory
layout (confirmed as a heisenbug: the same binary behaved differently
run directly vs. under gdb). Fixed (`c9c6670a`) by binding `t.value` to
an explicit `bval: str = t.value` local before iterating. Verified: the
full, unmodified `driver.py` now parses cleanly standalone (gen0-built
harness). Debug prints left in `sema.py`/`driver.py`/`program.py` from
the investigation were removed as part of the same cleanup.

Per user direction, next tried rebuilding gen1 via `--backend x86-64`
(the custom backend, intended to replace nasm for 2.0) instead of
legacy/nasm. This immediately hit a much bigger, separate gap: **`ir_
lower.py` has no comprehension lowering at all** (no `A.Comprehension`/
`A.DictComprehension` handling whatsoever) — flagged to the user rather
than silently expanding scope; user chose to fix a second discovered
blocker first (below), then return to comprehension lowering.

**Bug #12 — three real bugs in `program.py`'s whole-program merge,
found while diagnosing why `asmpython/__main__.py` failed whole-program
with ~20 "undefined variable" errors for module-level constants
(`ZIP_STORED`, `REGTYPE`, `BLOCKSIZE`, `_HEX_DIGITS`, etc.) even though
every source file compiles cleanly standalone** — affects both backends
equally, not just x86-64:

1. `_free_names`'s `A.DictComprehension` branch assumed `extra_for_vars`/
   `extra_for_targets`/`extra_for_iters`/`extra_for_conds` fields
   mirroring `A.Comprehension` — but `DictComprehension` has no such
   fields (only ever supports one `for` clause). An `AttributeError` on
   any merged dict comprehension — what actually surfaced first
   (`asmpython/__main__.py` itself has one, line 52).
2. `_class_free_names` — the function `_materialize_value_imports`'s
   `class_origin` mechanism depends on to auto-pull a module's own
   top-level constants into scope for a merged class's methods — was a
   stub that unconditionally `return set()`. Apparently a silent no-op
   since it was written; implemented it for real (walk each non-
   `@assembly_func` method body via `_free_names`, minus the method's
   own params).
3. The equivalent materialization never existed for plain top-level
   *functions*, only classes — e.g. `zipfile.py`'s functions referencing
   `ZIP_STORED` had no origin-tracking at all. Added `func_origin`
   (mirrors `class_origin`) and `_func_free_names`, plus a resolution
   pass over `entry.funcs` parallel to the pre-existing class loop.

(Commit `654181b0`.) This fixed most, but not all, of the "undefined
variable" errors — tracing individual cases with a temporary debug print
showed `resolve("ZIP_STORED", ...)` returning `True` (successfully
materialized and prepended to `entry.body`) yet the error still firing
downstream. A fourth, deeper bug:

**Bug #13 — `_check_block` had no per-statement error recovery, only
per-block (`_try_check_block`).** `_check_block` runs a plain `while`
loop calling `_check_stmt` per statement with no internal try/except;
`_try_check_block` wraps the *entire* call in one try/except. In
collect-errors mode (the CLI's default — `all_errors = not
args.one_error`), a `SemaError` on any ONE statement aborts every
statement after it in that block. The module-level body (`entry.body`)
is a single block holding every merged/materialized global — so one
early, unrelated failure meant every later global (including ones
bug #12 had already correctly resolved and prepended) never got
registered into `global_scope` at all. Fixed (`4886fa57`) by catching `SemaError`
per-statement inside `_check_block` when `self.collect_errors` is set,
logging + continuing to the next statement instead of letting the
exception propagate out of the whole block. This eliminated the entire
"undefined variable" cascade.

**Current blocker (not yet fixed): "mixed list element types
(instance:IRValue and str); mixed-type lists need a tagged-value
runtime, not yet implemented."** ~86 occurrences, all traced to `ir.py`'s
own `IRInstr.operands: list  # IRValue | int | float | str` — a field
*intentionally* documented as heterogeneous, now reachable by
whole-program sema for the first time now that bugs #12/#13 stopped
masking it. This is a real, pre-existing gap (heterogeneous/union-typed
lists aren't supported at all, only homogeneous ones), not a regression
from this session — it was always going to surface once the merge/
materialization bugs stopped hiding it. Two directions for next session:
(a) implement real heterogeneous-list support (a tagged-value runtime,
as the error names it) — large, general, benefits any future user
program with a similar shape; (b) narrow the fix to `ir.py` specifically,
changing `IRInstr.operands` to store everything as a uniform `IRValue`
(synthesizing "immediate" IRValues for raw int/float/str operands
instead of storing them bare) — smaller, more targeted, no language-
level feature needed. Given `ir.py`/`ir_lower.py` are core to the user's
stated 2.0 direction (x86-64 backend replacing nasm), and comprehension
lowering (the original ask, still not started) depends on the same
files, next session should decide (a) vs (b) with the user before
proceeding, then implement comprehension lowering in `ir_lower.py`.

Also landed this session, found along the way and unrelated to the
above chain: `project.py`'s `ProjectConfig.from_dict` used `set(cls.__
dataclass_fields__.keys())` and a dict-comprehension filter, both
outside asmpython's own compilable subset (dataclass introspection and
comprehensions over dict items aren't supported) — rewritten with an
explicit known-fields set and per-field reads (`3f3253be`). `ir_lower.py`
gained list/str slice lowering (`_abi_list_slice`/`_abi_str_slice`/
`_abi_list_slice_assign`, added to `abi_shims.asm`) and try/finally
handler-restore-on-return tracking (`548cdee3`), both pre-existing gaps
unrelated to the bugs above, closed opportunistically this session.

## Other Notes

- macOS Intel and RPi/Mac ARM64 are plan-steps 4 and 6-8 above, not
  independent side work — sequencing matters, see `[[project-2.0-versioning]]`.

## CLI restructure + package/project system (done, 2026-06-22/23)

Not part of the numbered 2.0.0 plan above — a separate, user-requested CLI
overhaul, fully implemented and test-suite-clean (454/455, same pre-existing
failure as always).

**New subcommand structure** (`asmpython/_compiler/__main__.py`):

- `asmpython build <source.py|project.json> [options]` — everything the old
  flat CLI did, plus accepting a project.json (see below) as the source.
  Bare `asmpython <file> [opts]` (no subcommand) still works — shorthand for
  `build`, implemented via an argv-preprocessing shim (`_preprocess_argv`)
  that injects `"build"` when argv[0] isn't a known subcommand or a
  top-level-only flag (`-h`/`-V`/`--explain`).
- `asmpython package install|uninstall <name|project.json> [options]` — see
  below.
- `asmpython project new [name] [--dir] [--target]` — scaffolds
  `project.json` + `main.py` + an empty `libs/` dir.

**Project manifest** (`asmpython/_compiler/project.py`, `ProjectConfig`):
JSON schema covering everything `build` needs — `name`, `entry`, `output`,
`target`, `output_type`, `bundle_mode`, `icon`, `use_runtime_lib`,
`library_dirs`, `packages`. CLI flags always override the matching
project.json field when given; otherwise the project's value is the
default. `find_default_project(cwd)` auto-detects a `project.json` sitting
directly in the current directory (no upward search) for `package`'s
no-`--dir` case.

**Package system** (`asmpython/_compiler/packages.py`): installs *native
runtime-library* dependencies (DLLs/.so/import libs — SDL2 and friends),
NOT Python packages. Resolution order: (1) `asmpython/_vendor/<name>/
<platform>/` — instant, offline, no network; this is how `sdl2` installs by
default today since it's already vendored. (2) A remote JSON registry
(`package-repository.json` at the repo root is the canonical format) keyed
by package name -> `{latest, versions: {version: {url, sha, verified}}}`.
Falls back to a small bundled dict (`_BUNDLED_REGISTRY`, just `sdl2`) if the
network/registry URL is unreachable. `sha` (sha256 of the downloaded
archive) and `verified` (maintainer-vetted) are independent, both optional:
hash mismatch hard-fails install; hash-confirmed-but-unverified or
no-hash-at-all both warn but proceed. Binaries are copied flat into a
*library directory* (`library_dirs[0]` from a project.json, else `./libs/`)
with a `.asmpython_packages.json` manifest recording exactly what was
installed, so `uninstall` removes precisely those files. Passing a
project.json instead of a bare name installs/uninstalls every name in its
`packages` list at once.

**Maintainer tool** (`fetch_package_hashes.py`, repo root): audits every
version in `package-repository.json` by actually downloading it and
comparing sha256 against the recorded `sha` — `OK`/`WARN` (missing)/
`MISMATCH` (wrong) per version; `--write` fills in missing hashes, `--write
--force` also overwrites mismatches. **Caught a real bug during this
work**: the registry's first SDL2 entry (release-2.0.14) has zero attached
GitHub release assets — the "download" was silently a 9-byte `"Not Found"`
error body (via `curl -sL` with no `-f` flag), and the checksum recorded
for it was garbage. Fixed by switching to `release-2.32.10`'s real
`SDL2-devel-2.32.10-mingw.tar.gz` asset with a verified-correct sha256 (run
`fetch_package_hashes.py` again before trusting any *future* registry
entry someone adds by hand — don't assume a URL that looks right actually
resolves to real content).

**Known cosmetic gap, not fixed**: em-dashes in argparse help text
(`_TOP_DESCRIPTION`, `_PACKAGE_DESCRIPTION`, etc.) render as `�` on a
non-UTF8 Windows console (cp1252) — pre-existing behavior from before this
rewrite too, not a regression, just never addressed.
