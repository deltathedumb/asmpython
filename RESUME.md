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
`str.format()`, `Global`/`Nonlocal` (blocked on `.bss`/box-pointer
addressing not yet in the IR), f-strings, classes/instance
methods/dunders, closures, generators, match statements, `for` over
set/zip/enumerate/instance iterables, `enumerate()`/`zip()` as
standalone values (deferred — codegen only handles these inside for-loop
iteration), `list.sort(key=...)`/`reverse=...`, `dict.copy()`/
`setdefault()`, `set.union`/`intersection`/`difference`, int-element
sets, comprehensions with tuple-unpack targets/multiple `for` clauses/
non-list-or-tuple iterables, `isinstance()` with a tuple-of-classes or
class-name target (needs class-id tracking FuncCtx doesn't have yet).

**Done and committed** (latest commit `0fd427e9`):

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
and list comprehensions are all landed. Remaining natural targets:
f-strings / `str.format()`, `Global`/`Nonlocal`, classes/instance
methods/dunders, stepped slices, list slices. Classes are the biggest
remaining unit (method resolution, dunder dispatch, instance layout) —
worth tackling f-strings and Global/Nonlocal first since they're more
contained. Once the remaining surface is substantially covered, move to
plan-step 2 (register allocator). Before starting classes specifically,
worth checking in on scope/pace given how much real design nuance this
file has accumulated.

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
