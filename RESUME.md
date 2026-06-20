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

**Next step on resume**: the builtin-wrapper punch list, string slicing,
list comprehensions, `Global`/`Nonlocal`, and basic f-strings are all
landed. Remaining natural targets, roughly in order of size: `str.
format()` and format-spec'd f-string segments (reuse codegen.py's
`_cfmt_for_spec`/alignment logic as a guide), stepped/list slices, then
classes/instance methods/dunders (the biggest remaining unit — method
resolution, dunder dispatch, instance layout). Once the remaining
surface is substantially covered, move to plan-step 2 (register
allocator). Before starting classes specifically, worth checking in on
scope/pace given how much real design nuance this file has accumulated.

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

**Bug #7 — OPEN, not yet root-caused**: broader coverage
(`tests/cases/03_fib.py`, and a minimal `def f(n): return n; x = f(5);
print(x)` repro) still crashes the selfhosted binary — `strlen()`
called with the literal pointer value `8` (not a real string),
originating somewhere inside `Codegen.__gen_call`'s plain
user-function-call path while compiling `f(5)`. Confirmed via
`--check` that this is purely a codegen-phase bug (sema passes clean).
Confirmed NOT present via the Python-hosted compiler (runs correctly,
prints `5`). Investigation got as far as: `_gen_call` is entered
exactly once (for the one call in the file) and crashes before
returning; the crash is unrelated to `print()` itself (a breakpoint on
`_gen_print` never fires). Likely candidates not yet checked: `_emit_
positional_args`/`_eval_call_operands`/`_load_call_operands` (the
1-argument register/stack-spill path — note 0-argument calls like
`def f(): return 5; x = f()` do NOT crash, narrowing it to something
specific to ≥1 argument), or `_user_symbol`/`self.funcs` dict-membership
check right before dispatch. Same gdb methodology as bugs #1-#6 applies
— `break Codegen___gen_call`, then single-step or break on nested
helper entry points (`Codegen___emit_positional_args`,
`Codegen___eval_call_operands`, `Codegen___load_call_operands`) to
narrow which one diverges. Minimal repro saved as a pattern (not a
checked-in file): `def f(n): return n` + `x = f(5)` + `print(x)` is
the smallest known trigger.

**Toolchain note for future selfhost testing sessions**: the `build/`
directory's many generated `.exe` files got externally wiped mid-session
(likely Windows Defender or similar quarantining freshly-built,
unsigned executables) — if `build/asmpython_v*.exe` binaries vanish
unexpectedly, that's the likely cause; just rebuild. Also: avoid output
filenames containing "update"/"install"/"setup"/"patch" — Windows
flags them for UAC elevation, which hangs non-interactive runs.

## Other Notes

- macOS Intel and RPi/Mac ARM64 are plan-steps 4 and 6-8 above, not
  independent side work — sequencing matters, see `[[project-2.0-versioning]]`.
- (Deferred, only if user revisits) A `.csproj`-style project
  manifest/build-orchestration system for asmpython programs — asked
  about once, acknowledged as a real but separate idea, no work started.
