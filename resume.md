# Resume notes — autonomous CPython-parity loop (parity-expansion branch)

## Status as of 2026-06-14

- 215/215 tests passing (`py -m tests.runner`).
- Branch is clean and pushed: `origin/parity-expansion` is up to date with
  local `HEAD` (`313d977`).
- Recently landed (most recent last):
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

### Known compiler bug: `for a, b in <list of custom-class instances>` segfaults

Discovered while testing `Counter.most_common()`: CPython's real
`Counter.most_common()` returns a `list[tuple[str, int]]`, so
`for el, cnt in c.most_common(): ...` unpacks tuples. asmpython's
`most_common()` returns `list[CountPair]` (a plain class, see
`collections.py`'s `CountPair`), and `for el, cnt in <list[CountPair]>: ...`
**segfaults (exit 139)** when compiled, instead of either working (treating
it like an iterable-unpack via `__iter__`/`__getitem__`) or raising a clear
compile error. Root cause is the same general gap as the `_scan_tuple_return`
pre-pass timing issue (sema.py ~1734-1759): there's no `dict.items()`-style
hardcoded `tuple_elem_types` mechanism for arbitrary user/stdlib methods that
return `list[tuple]`. Two possible fixes: (a) special-case
`Counter.most_common()`'s return type the way `dict.items()` is special-cased,
or (b) make `for a, b in <list[T]>` either a compile error when `T` isn't a
tuple type, or correctly call `T.__iter__`/unpack via `__getitem__(0)`/
`__getitem__(1)` — (b) is the general fix and more in the spirit of "extend,
don't special-case". Not fixed yet; `tests/cases/167_counter_operators.py`
deliberately avoids this pattern (only tests `+`/`-`/`&`/`|`, not
`most_common()`).

Modules CPython has that asmpython doesn't (breadth backlog, roughly
priority order — verify each is still missing before starting, and check
whether it's even meaningful for a systems/assembly-targeting compiler):

1. `datetime`/`time` extensions — `time.py` exists but is libc-wrapper only;
   a pure-Python `datetime`-like date/time arithmetic module would be real
   breadth.
2. `base64` — pure string/byte manipulation, good fit.
3. `uuid` — needs random bytes (random.rand exists) + hex formatting.
4. `fractions`/`decimal` — exact-arithmetic types; decent fit if `class`
   and operator-overloading dunders are solid (check `__add__` etc. coverage).
5. `glob`/`shutil`/`tempfile` — depend on `os`/`pathlib` which already exist;
   check what os/pathlib primitives are missing first.
6. `logging` — could be a thin wrapper over `print`/`sys.stderr`.
7. `unittest` — large; lower priority (asmpython has its own
   `tests/runner.py` harness already).

(`csv` landed in `e9e9525` — removed from this list.)

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

`asmlib/gui.py` and `asmlib/network.py` have NOT been surveyed yet — could
be a future breadth item if there's appetite (network.py wraps
sockets/Winsock2, gui.py wraps a minimal framebuffer; check for stub-only
functions the way hardware.py was checked).

## Next steps

1. Check `.claude/issues` (empty so far).
2. Pick the next item from the stdlib backlog above. Good next candidates,
   roughly in order of value/effort:
   - The `for a, b in <list of custom-class instances>` segfault documented
     above — investigate the general fix ((b) in that section) since it
     likely affects other stdlib methods returning `list[SomeClass]` too,
     and the `list[list[T]]`/`list[dict[K,V]]` nested-generic gap that
     shaped the `csv` module's API (`e9e9525`).
   - `base64`, `uuid`, `fractions`/`decimal` from the breadth backlog below.
   - Survey `asmlib/gui.py` / `asmlib/network.py` for stub-only functions
     (not yet checked, unlike `hardware.py` which is now done).
3. Follow the standing per-feature workflow (above) for whatever is picked.
