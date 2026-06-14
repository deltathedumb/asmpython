# Resume notes — autonomous CPython-parity loop (parity-expansion branch)

## Status as of 2026-06-14

- 218/218 tests passing (`py -m tests.runner`).
- Branch is being pushed: `origin/parity-expansion` should track local
  `HEAD` (latest: `list[tuple[T1,T2]]` return-annotation slot-type
  propagation + `Counter.most_common()` -> real `list[tuple[str,int]]`, on
  top of `35267d3`).
- Recently landed (most recent last):
  - (this session) — fixed the long-standing `-> list[tuple[T1,T2]]`
    annotation gap: the parser/sema previously collapsed
    `list[tuple[str,int]]` to a bare `("list","tuple")`, losing the per-slot
    kinds, so `for a, b in f()` (and `f()[i][j]`) mis-typed both unpack
    targets as `"any"` (the `str` slot printed as a raw pointer). Now
    `parser._normalize_annot` keeps `("list", ("tuple", ["str","int"]))`,
    `sema._resolve_annot` resolves it into `FuncSig.ret_list_tuple_types`,
    and `A.Call`/instance `A.MethodCall` sites stamp `tuple_elem_types` on
    the call node. This unblocked rewriting `collections.Counter.
    most_common()` to return real `list[tuple[str,int]]` (was
    `list[CountPair]`); `CountPair` removed. `tests/cases/
    151_collections_module.py` extended to cover `mc[0][0]`/`mc[0][1]` and
    `for el, cnt in c.most_common(): ...`.
  - `1673c49` — new `console_*` high-level API in `asmlib.hardware`
    (`console_clear/putc/write/set_color/set_cursor/get_row/get_col`):
    real VGA text-mode ops on `--target freestanding` (thin wrappers around
    `print()`'s existing `_vga_*` helpers), ANSI/VT100 escapes + tracked
    cursor state on hosted Windows/Linux (new `Codegen._emit_console_runtime`
    in `codegen.py`, shared by both hosted targets). New
    `tests/cases/171_hardware_console.py` (output includes raw ANSI escape
    bytes, captured from a real run).
  - (this session) — fixed the `for a, b in <list of custom-class
    instances>` segfault documented below: now a compile-time
    `cannot unpack non-iterable {ClassName} object` `SemaError`, mirroring
    CPython's `TypeError`. New
    `tests/cases_fail/for_unpack_non_iterable_instance.py`.
  - `462d204` — new `uuid` module (`UUID`, `uuid4`); fixed `repr()` on user
    instances to dispatch `__repr__`/`__str__` (was printing a raw pointer);
    fixed `a == b`/`a != b` between instances to dispatch a user `__eq__`
    (was raw pointer comparison). Also fixed pre-existing env flakiness in
    `169_hardware_real_ops.py` (`t1 > 0` -> `t1 != 0`, rdtsc's top bit can be
    set, signed compare misreads it).
  - `ff6b439` — `match`/`case` structural pattern matching (PEP 634).
  - `efb3f08`..`1aa9b2f` — large stdlib breadth wave: string/collections/
    itertools/functools/json, os expansion, re + raw strings + sys expansion,
    io.StringIO/operator/copy, enum/abc/contextlib, struct/hashlib/heapq/
    bisect/statistics/typing/dataclasses/textwrap.
  - `c8fddc3` — 4 correctness bug fixes found while testing the breadth wave:
    Windows `print(0.0)` -> `inf` (NASM 64-bit immediate truncation in the
    inf/NaN check), `float + <element of an unannotated list>` mistyped as
    `any` (spurious extra `cvtsi2sd` corrupting accumulation loops),
    `return <int/any expr>` from a `-> float` function not promoting to
    `xmm0` (new `FuncInfo.ret_is_float`), `textwrap` `-> list` annotations
    that should have been `-> list[str]`. Plus 5 stale `# expect:` fixes.
  - `402f990` — `collections.OrderedDict.move_to_end()` / `.popitem()`
    implemented; fixed `OrderedDict.keys()`/`defaultdict.keys()` being
    declared `-> list` (opaque) instead of `-> list[str]`.
  - `ce9977c` — `collections.Counter.__add__`/`__sub__`/`__and__`/`__or__`
    implemented (CPython multiset semantics, drop non-positive results).
  - `e9e9525` — new `csv` module: `reader`/`Row`, `writer_row`/`writer_rows`,
    `DictReader`. `reader()` returns `list[Row]` (not `list[list[str]]`) —
    asmpython's flat list-element-type system can't express nested generics
    (confirmed via `_scratch_nested.py`: `for f in row` over a
    `list[list[str]]` element prints raw pointers, same root cause as the
    `most_common()` segfault below).
  - `313d977` — `asmlib.hardware.rdtsc()`/`cpuid()`/`rdrand()` now execute
    real instructions on hosted Windows/Linux targets (previously hardcoded
    to return 0 there, same as the genuinely ring-0-only ops like port I/O,
    MSRs, `halt`). `target_windows.py`/`target_linux.py` emit the real
    `rdtsc`/`cpuid`/`rdrand` sequences in `emit_asmlib_runtime`'s `needs_hw`
    block.

## Standing directives (always apply)

- "extend asmpython to support, don't edit to make compatible"
- "don't make minimal versions: go full for everything"
- never write `-> "ClassName"` quoted forward-ref annotations (parser treats
  STRING annotations as unconstrained `any`)
- commit at checkpoints; **push after each commit** to `origin/parity-expansion`
- regularly check `.claude/issues` for new failing repro cases (empty so far)
- workflow per feature/fix: implement -> CPython-verified `_scratch_*.py`
  comparison -> add `tests/cases/*.py` (`# expect:`) or
  `tests/cases_fail/*.py` (`# expect-error:`) -> `py -m tests.runner` green ->
  update `docs.html`/`CHANGELOG.md` if user-visible -> commit -> push.
- **"i want breadth"** — stdlib / asmlib / asmlib.hardware should be "fully
  production ready and equal to python". This is the active multi-session
  focus (see survey + backlog below).

## Stdlib survey (asmpython/stdlib/, sizes in lines)

Substantial / mature: collections.py (337+), hashlib.py (398), re.py (420),
argparse.py (437), json.py (284), itertools.py (269), pathlib.py (262),
statistics.py (248), struct.py (247), textwrap.py (216), typing.py (142),
operator.py (134), heapq.py (133), math.py (115), os.py (105),
asmlib/hardware.py (99), enum.py (91), ospath.py (92).

Thin / stub-heavy, candidates for expansion:

- `functools.py` (47): `reduce`, `lru_cache`/`cache`/`wraps` are pass-through
  stubs (no real memoisation — would need persistent storage + hashing of
  args, nontrivial). `partial` not implemented — **documented language
  limitation**: asmpython can't store arbitrary callables in fields/locals
  and call them later. Revisit only if/when closures become supported.
- `contextlib.py` (34): `contextmanager`/`suppress`/`nullcontext` are stubs.
  `suppress()` becoming real would need the `with`-rewrite (sema.py, the
  `A.With` -> `A.Try` lowering from `58fd662`) to (a) pass real exception
  info to `__exit__`, and (b) treat a truthy `__exit__` return as
  "suppress, don't re-raise" — currently it's a plain `try/finally`. Real
  but scoped follow-up if `with`/exceptions need to interact.
- `dataclasses.py` (60): `@dataclass` itself is handled natively by the
  compiler (sema synthesizes `__init__` etc.) — `fields()`/`asdict()`/
  `astuple()`/`replace()`/`is_dataclass()` are stubs. Low priority unless a
  real test needs them.
- `abc.py` (23), `copy.py` (24), `string.py` (25), `random.py` (29),
  `sys.py` (30): small but functionally complete for their common-case API.
  `random` lacks `choice`/`shuffle`/`sample` (need generic-over-element-type
  support — asmpython has no generics, so a `list[T]` -> `T` signature isn't
  directly expressible; would need per-callsite specialization or `any`
  results, investigate before committing).
- `collections.py` remaining gaps: `deque` has no `maxlen`; `Counter`'s
  `most_common()` doesn't tie-break by insertion order like CPython;
  `namedtuple` has no `defaults=`. (`+`/`-`/`&`/`|` operators landed in
  `ce9977c`.)

### Fixed: `for a, b in <list of custom-class instances>` segfault -> compile error

Discovered while testing `Counter.most_common()`: CPython's real
`Counter.most_common()` returns a `list[tuple[str, int]]`, so
`for el, cnt in c.most_common(): ...` unpacks tuples. asmpython's
`most_common()` returns `list[CountPair]` (a plain class, see
`collections.py`'s `CountPair`), and `for el, cnt in <list[CountPair]>: ...`
used to **segfault (exit 139)** when compiled (`_gen_for_list` dereferenced
the `Pair` instance pointer as if it were a list/tuple buffer). Fixed
(this session, part (b) of the two options below): sema now raises
`cannot unpack non-iterable {ClassName} object` (matching CPython's
`TypeError`) at compile time whenever `for a, b in <list[T]>` has a `T` that
is a plain user class. New
`tests/cases_fail/for_unpack_non_iterable_instance.py`.

Fixed (this session): `Counter.most_common()` now returns real
`list[tuple[str, int]]` (see "Recently landed" above) — `for el, cnt in
c.most_common(): ...` and `c.most_common(2)[0][0]` both work and match
CPython. `tests/cases/167_counter_operators.py` still only tests
`+`/`-`/`&`/`|` (not `most_common()`); `151_collections_module.py` covers
`most_common()`.

Modules CPython has that asmpython doesn't (breadth backlog, roughly
priority order — verify each is still missing before starting, and check
whether it's even meaningful for a systems/assembly-targeting compiler):

1. `datetime`/`time` extensions — `time.py` exists but is libc-wrapper only;
   a pure-Python `datetime`-like date/time arithmetic module would be real
   breadth.
2. `base64` — pure string/byte manipulation, good fit.
3. `fractions`/`decimal` — exact-arithmetic types; decent fit if `class`
   and operator-overloading dunders are solid (check `__add__` etc. coverage).
4. `glob`/`shutil`/`tempfile` — depend on `os`/`pathlib` which already exist;
   check what os/pathlib primitives are missing first.
5. `logging` — could be a thin wrapper over `print`/`sys.stderr`.
6. `unittest` — large; lower priority (asmpython has its own
   `tests/runner.py` harness already).

(`csv` landed in `e9e9525`, `uuid` in `462d204` — removed from this list.)

## asmlib / asmlib.hardware

`asmpython/asmlib/{__init__,hardware,gui,network}.py`. Surveyed in full this
session (`313d977`): of the ~30 `_hw_*` bindings, only `rdtsc`/`cpuid`/
`rdrand` are unprivileged (ring-3) instructions — these now execute for real
on hosted Windows/Linux too (previously hardcoded to 0). Everything else
(port I/O, MMIO, MSRs, CR0-4, invlpg, lidt, cli/sti/hlt, PIC/PIT, keyboard,
VGA text mode) genuinely requires ring 0 and is **correctly** a
zero-returning no-op on hosted targets — these are not bugs, just
inherently freestanding-only (real implementations already exist in
`target_freestanding.py`, exercised by the `freestanding16` BIOS-boot target
from `1e8a208`/`3caeb20`). `asmlib.hardware` is now considered
"production ready" for its stated scope — no further action needed unless a
new hardware primitive is requested.

This session, per the user's explicit request ("make sure asmlib.hardware
has both low-level interfaces and high-level interfaces (like console
printing handlers and basic rendering)"), added a high-level `console_*`
layer on top of the low-level primitives above: `console_clear/putc/write/
set_color/set_cursor/get_row/get_col`. Real everywhere — VGA text-mode on
freestanding (wraps the same `_vga_*`/`print()` internals), ANSI/VT100
escapes + tracked cursor state on hosted Windows/Linux. If more "basic
rendering" is wanted later (e.g. a `console_fill_rect`/box-drawing helper),
this is the layer to extend.

`asmlib/gui.py` and `asmlib/network.py` have NOT been surveyed yet — could
be a future breadth item if there's appetite (network.py wraps
sockets/Winsock2, gui.py wraps a minimal framebuffer; check for stub-only
functions the way hardware.py was checked).

## Next steps

1. Check `.claude/issues` (empty so far).
2. Pick the next item from the stdlib backlog above. Good next candidates,
   roughly in order of value/effort:
   - The `list[list[T]]`/`list[dict[K,V]]` nested-generic gap that shaped the
     `csv` module's API (`e9e9525`) — same family as the `list[tuple[T1,T2]]`
     gap just fixed (this session), but for `list`/`dict` element kinds
     instead of `tuple`. Would let `csv.reader()` return `list[list[str]]`
     directly instead of `list[Row]`.
   - `base64`, `fractions`/`decimal` from the breadth backlog below.
   - Survey `asmlib/gui.py` / `asmlib/network.py` for stub-only functions
     (not yet checked, unlike `hardware.py` which is now done).
3. Follow the standing per-feature workflow (above) for whatever is picked.
