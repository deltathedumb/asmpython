# 2.0.0 Resume

Full historical detail for everything summarized in this file is recoverable
via `git log -p -- RESUME.md`. This file is intentionally kept short — it's a
pointer to *current state*, not a full session-by-session journal.

## Directive

Original scope (2026-06-18): "Continue dev until we hit 2.0.0 ready" —
garbage collector, optimizations, selfhost-capable, ARM support, Mac support
(Intel + Apple Silicon), Raspberry Pi support (OS + bare metal).

**Expanded 2026-07-15** with a large, explicitly-confirmed workload addendum
(not yet started — see "Pending 2.0.0 workload" below) and a completed
compiler-extension-system feature (see "Extension System" below, done first
per the user's own sequencing choice: finish the bounded, independent
extension work before touching roadmap docs or scoping the larger program).

See `docs/ROADMAP.md`'s 2.0.0 section for the platform/perf/ecosystem
breakdown. The numbered work order below is day-to-day sequencing.

**Confirmed work order** (real dependency chain, user-confirmed):

1. IR migration (see "IR Migration Status" below).
2. Linear-scan register allocator.
3. New backend lowering; validate parity vs the test suite.
4. macOS Intel x86-64 target.
5. ARM64 lowering (Linux first).
6. macOS Apple Silicon.
7. Raspberry Pi Linux.
8. Raspberry Pi bare metal (freestanding ARM64 — new work).
9. Garbage collector (refcounting).
10. Optimization passes beyond the existing peephole dead-store pass.
11. **Selfhost** (asmpython compiling itself) — see "Selfhost Status" below.
12. Stdlib completion — see "Stdlib Status" below.
13. Release pass: CODE_OF_CONDUCT/CONTRIBUTING/SECURITY/issue templates,
    CHANGELOG, version bump off `-preview`.

**Newly confirmed, not yet sequenced into the numbered list above** (2026-07-15,
see "Pending 2.0.0 workload" below for the full item list): finish the
IR-based x86-64 backend as the default replacing the legacy backend, embed
pyinbin into produced executables for native/interpreted hybrid execution,
formalize the backend system as a versioned SDK, real memory management
(refcounting/ownership/cycles), deterministic fixed-point self-hosting.
User's explicit instruction attached to this addendum: "stop expanding
partial stdlib breadth until these foundations are complete."

**Tangential, NOT active work**: user is also building **uASM**, a modular
machine-code compiler with swappable backends/frontends, currently depending
on asmpython. Plan: finish 2.0.0 first, fork asmpython into a uASM-facing
frontend afterward, as a separate effort. Don't let this influence IR/backend
design decisions above.

## Extension System — DONE (2026-07-15, commit `1a40ee30`)

Added `extend <name>` / `retract <name>` / `const NAME [: annot] = value` to
the native compiler (`asmpython/_compiler/`, not pyinbin) as real contextual-
keyword syntax across lexer/parser/AST/sema/shared-IR/legacy-codegen — not
runtime calls, not source preprocessing. Full design: `docs/EXTENSIONS.md`.

Key points for anyone touching this next: new `extensions.py`
(`ExtensionContext`/`CompilerExtension`, transactional activate/retract,
fresh per-`Parser` instance, so isolation across modules/invocations is
automatic); `const` locks a name against every rebinding form via one shared
`sema.py` helper (`_require_assignable`) but leaves the referenced object's
own mutability untouched; `ConstDecl` normalizes to a plain `Assign` at the
top of `ir_lower.py`/`codegen.py`'s dispatchers, so the IR-based x86-64/
ternary backends needed zero extension-specific code. One known, deliberately
unfixed scope gap: a couple of `program.py` whole-program-merge helpers
(`_simple_const_if_targets`, an "already available" dedup check) still don't
recognize `ConstDecl` — only matters for platform-conditional `const` hoists
across module boundaries, not the 44 shipped test scenarios.

**Side-effect fix**: `tests/runner.py`'s negative-test runner was missing
`--no-pyinbin-fallback`, so every `cases_fail` test (not just this feature's
new ones) was silently checking the pyinbin fallback's unrelated error text
instead of the real native-compiler diagnostic. Fixed — see corrected test
baseline below.

## IR Migration Status — architecture changed mid-flight

**Original plan (superseded)**: a from-scratch SSA IR builder, `ssa_build.py`,
built incrementally alongside `ir.py`/`ir_builder.py`, targeting a
to-be-written `X86_64Target` lowering that would eventually replace
`codegen.py`'s direct-emission entirely. Got substantial coverage before
being superseded. **`ssa_build.py` no longer exists in the tree.**

**Current approach**: `asmpython/_compiler/ir_lower.py` lowers the AST
directly to `ir.py`'s IR, feeding a real custom backend under
`asmpython/_backends/`:

- `_backends/x86_64/`: custom encoder, register allocator (`regalloc.py`),
  phi elimination (`phi_elim.py`), COFF/ELF/PE readers and linkers — replacing
  NASM + gcc as the assemble/link step, wired up via `--backend x86-64`.
- `_backends/ternary/`: an early speculative ISA backend (uASM-related).

**Last confirmed status** (2026-07-16, commits `fc688dfd`..`HEAD`):
smoke-tested `--backend x86-64` against the first 60 `tests/cases/*.py` (not
part of the automated suite, which exercises the legacy backend only):
**48/60 compiled+linked** (up from 34/60 at session start). `tests.runner`
still 481/489 throughout, no regressions at any checkpoint. Full list of
fixes this session, roughly in dependency order:

- A real general bug where any top-level `for x in <list>:` wrote its loop
  variable to an unused local stack slot while every read resolved it as a
  module global, so the variable always read back as zero — silently
  corrupting any module-scope for-loop. Fixed by routing the single-target
  write through the same `_store_loop_target`/`_name_ptr` helper the
  tuple-unpack/`enumerate()` loop forms already used correctly.
- Missing `round`/`divmod`/`hex`/`oct`/`bin`/`bool`/`repr`/`input` builtins
  and a broken `pow(int,int)` (routed through msvcrt's double-only `pow`,
  corrupting output) — all were falling through to a blind "assume it's a
  real DLL symbol" call path.
- `print()`/`str()` only handled str/int/float, printing lists/dicts/
  tuples/instances as raw pointer values and `True`/`False`/`None` as
  `1`/`0`/wrong-path text — both now delegate to `_lower_expr_as_str`, the
  repr helper f-strings already used; added a proper heterogeneous tuple
  repr (`"(a, b)"`, 1-tuple trailing comma) alongside the existing uniform
  list/dict/set repr.
- Instance `MethodCall` built its symbol from the receiver's *static* type
  instead of walking the inheritance chain (an inherited-but-unoverridden
  method/property never linked); fixed, and separately added real virtual
  dispatch (a base-class method calling an overridden method needs the
  receiver's *runtime* `__class__` id, not its static type) — ported
  `codegen.py`'s `_virtual_dispatch_rows` design as a pre-built chain of
  check/hit IR blocks.
- Mixed int+float arithmetic (`a + b` where one side is int, other float)
  emitted `fadd` directly on an un-promoted i64 operand with no `sitofp` —
  fixed for both `BinOp` and `AugAssign`.
- A user-function/lambda call's result was unconditionally typed `i64`
  regardless of the callee's real return type, corrupting every
  float-returning function call (garbage via wrong register class) — fixed
  to use the call expression's own inferred type.
- **New `bitcast_i2f`/`bitcast_f2i` IR ops** (raw 64-bit GP↔XMM bit-move,
  `MOVQ`, not a numeric `sitofp`/`fptosi` conversion) plus two new x86-64
  encoder functions, to fix the whole class of "float value stored through
  an int-only 8-byte cell" bugs: instance attributes, dict literals/`.get()`
  default, list literals, `IndexAssign` into a dict — all `_abi_dict_set`/
  `_abi_list_append`-based storage only ever moves GP-sized values, so a
  raw F64 `call` arg silently corrupted the marshaled register. (Direct
  memory `load`/`store` of a float slot, e.g. list/tuple element read,
  needed **no** bitcast — bits are bits in memory regardless of which mov
  variant touches them; only the GP/XMM *register* crossing needs one.)
- New `_abi_round_f64`/`_abi_divmod`/`_abi_input`/`_abi_float_to_str`/
  `_abi_fmax_f64`/`_abi_fmin_f64` shims in `abi_shims.asm`.
  `_abi_float_to_str` ports `codegen.py`'s NaN/inf detection +
  `%g`-then-append-".0"-if-no-decimal-point fixup, fixing bare
  `print(float)`/`str(float)` (was printing C's bare `"2"` instead of
  Python's `"2.0"`, or UCRT's `"1.#QNAN"` instead of `"nan"`).
  `fmax`/`fmin` route through `MAXSD`/`MINSD` directly (neither is a real
  msvcrt.dll export under any spelling); `exp2` rewrites to `pow(2.0, x)`
  at the IR level for the same reason.
- `pe_linker.py`'s `_DLL_FOR_SYMBOL`/`_SYMBOL_ALIASES` gained `floor`/
  `ceil`/`difftime` (real exports, verified against the live system
  `msvcrt.dll`'s export table, not just NASM emitting the call) and
  `copysign`→`_copysign`/`hypot`→`_hypot` aliases (MS-prefixed spelling,
  same pattern as the existing `access`/`strtoll` aliases). `trunc` has no
  real or aliased export at all — rewritten to a bare `fptosi` at the IR
  level (`trunc(x)` IS "truncate toward zero", no libm call needed).
- Float `%`/`**` binops (both `BinOp` and `AugAssign`) route to real libc
  `fmod`/`pow`; int `**` reuses the same non-negative-exponent multiply
  loop as the `pow()` builtin (extracted into a shared `_lower_int_pow`
  helper).

Spot-checked full build+run+expected-output byte-for-byte match on every
case fixed this session (100, 101, 106, 108, 109, 117, 118, 119, 120, 121,
122, 124, 127) — all exact except **109_pct_format.py segfaults at
runtime** (compiles now, previously didn't reach that far) — `%`-style
string formatting (`"%s is %d" % (...)`) has zero implementation in this
backend, a large separate feature, not attempted this session.

**Follow-up session** (same day, commits after `f4cdf6ba`): the
"03/04/12/115 print zeros" symptom flagged above turned out to be **two
separate bugs, not the module-scope-global one re-appearing**:

1. `03_fib.py`/`04_loops.py`/`12_list_grow.py` use `for i in range(...):`
   (not a list literal) at module scope — a *third* lowering path
   (`A.For` with `s.range_args`, distinct from both the list-`For` path
   fixed earlier and the `enumerate`-`For` path) had the exact same
   `ctx.ensure_slot()`-instead-of-`_name_ptr()` bug. Fixed the same way.
2. `115_loop_else.py`: **every** loop form with an `else` clause (`While`,
   range-`For`, list-`For`, `enumerate`-`For`) routed `break` to the same
   block that unconditionally ran the `else` body next — so `else` always
   ran even after a `break`, when Python's for/while-`else` should only
   run on natural exhaustion. Fixed by giving each loop form a separate
   `natural` block (condition-false edge) that falls into the `else` body
   then into a distinct `end` block, while `break` (via `loop_stack`)
   targets `end` directly, bypassing `else` — ported `codegen.py`'s
   `top`/`nat`/`end` three-label design as three-block chains.

Also found and fixed a **third, unrelated, pervasive bug** while sweeping
for more: plain `idiv`/`irem` (the IR ops backing `//`/`%`) truncate
toward zero (raw x86 `IDIV` semantics) with no floor-toward-`-inf`
correction, so any negative-operand `//`/`%` was silently wrong (`-7 // 2`
gave `-3` instead of `-4`). Fixed via a new shared `_lower_int_floordivmod`
helper (ported `codegen.py`'s/`_runtime_divmod`'s identical
sign-mismatch-then-adjust correction as IR blocks) used by both `BinOp`
and `AugAssign`.

Verified: `tests.runner` still 481/489 (no regressions). Full
build+run+expected-output sweep on 1-60 went 29/60 → 34/60 exact matches
across this follow-up (was 21/60 before the whole day's work started).

**Second follow-up** (same day, commits after `9f867684`): fixed the
`(null)` exception-message bug (114/131) — it was **two** more bugs, both
found by tracing IR/runtime state directly rather than guessing:

1. A module-scope `except X as e:`'s `bind_name` write used
   `ctx.ensure_slot()` (always local) while `_collect_module_globals`
   correctly registers `A.Try.bind_name` as a module global and every
   *read* of `e` resolved through that global — the fourth occurrence of
   this exact write/read mismatch bug class found this day (for-loop
   vars, range-for, now exception bindings). Fixed the same way
   (`_name_ptr` instead of `ensure_slot`).
2. Bare `raise` with no active exception (`_runtime_exc_msg` still NULL,
   nothing ever raised) blindly forwarded the NULL message/stale type id
   instead of substituting CPython's `RuntimeError("No active exception
   to reraise")` — `codegen.py` has this exact NULL-check/substitution
   that `ir_lower.py`'s bare-raise path never had. Ported it as an IR
   block (has-exception / substitute-then-raise / merge).

Verified: `tests.runner` still 481/489. Full build+run+expected-output
sweep on 1-60 went 34/60 → 36/60 exact matches.

Remaining 1-60 build failures: several unimplemented `MethodCall`s
(`list.sort`, `dict.pop`, `str.rpartition`/`casefold`/`format`), `del`
statement, starred/non-Name tuple-assign targets, walrus operator,
`sorted(key=...)` lambda body. Remaining build+run mismatches on 1-60 (all
re-confirmed pre-existing, not new): `132_dict_union.py`/
`133_dict_unpack.py`/`104_set_methods.py`/
`123_set_discard_remove_copy_pop.py` produce empty or wrong output;
f-string format-spec support (alignment, width, precision,
thousands-grouping, binary/hex-with-prefix) is entirely unimplemented
(silently ignores the spec and prints the plain value); `109_pct_format.py`
/`111_pct_repr.py` segfault (`%`-style string formatting has zero
implementation, a separate large feature).

**Next step on resume**: `del` statement and the tuple-assign/
starred-target gaps look like the next-cheapest build-success wins.
Given this session found the same write/read global-vs-local mismatch
bug **four separate times** in four different lowering sites (module-
scope for-loop var, range-for var, exception bind_name, and originally
the list-for var), it's worth a **grep audit**: search `ir_lower.py` for
every remaining bare `ctx.ensure_slot(<name>, ...)` call where `<name>`
is a source-level identifier (not a compiler-internal `__foo_{id}` temp)
and verify each one goes through `_name_ptr` instead, rather than waiting
to find the fifth occurrence via another failing test case. F-string
format specs are a bigger, self-contained feature (worth a dedicated
pass: parse the `:spec` mini-language once, drive alignment/width/
precision/base/grouping from one shared formatter) that would close out
most of the remaining fstring_* cases at once. All of this is toward full
parity with `codegen.py` under `--backend x86-64` — the direct
predecessor of the newly-confirmed "make the IR-based backend the
default" workload item.

## Selfhost Status (plan-step 11)

Selfhost = asmpython (gen0, built by CPython) compiling its own source to
produce gen1, and gen1 compiling the same source again to produce gen2.

**Last confirmed blocker**: gen1 compiling `asmpython/__main__.py` produces a
gen2 `.asm` truncated to ~4,426 lines (vs. gen0's ~510,000+), zero function
labels emitted, failing to assemble. Not yet root-caused — likely
`program.py`'s whole-program-merge import closure silently failing to expand
for gen1 specifically. Two other known-open issues found along the way:
`import os` (alone, unused) segfaults gen1 specifically; `isinstance(x, T)`
in gen1-produced code always returns `False` for the *first*-declared class
in a program (2nd/3rd+ classes work).

A long chain of real bugs (set operators, closure free-var type inference,
Win64 shadow-space corruption, boolop truthiness on containers/floats,
opaque-`any`-typed-as-wrong-shape bugs, `copy.deepcopy` resolving to
asmpython's own list-only stdlib shim under self-hosting, three
`program.py` whole-program-merge bugs, missing per-statement error recovery)
were found and fixed across earlier sessions — full list in `git log -p --
RESUME.md` on an older commit if needed.

**Toolchain notes**: `build/*.exe` binaries can get externally wiped
(Windows Defender quarantining unsigned freshly-built exes) — just rebuild.
Avoid output filenames containing "update"/"install"/"setup"/"patch"
(triggers UAC). Space-containing toolchain paths aren't handled by
`_resolve_tool`'s `Path.is_file()` — use space-free copies.

## Stdlib Status (plan-step 12)

Most of the user's explicitly-requested module list is complete or
rewritten: `abc`, `argparse`, `array`, `base64`, `binascii`, `collections`,
`contextlib`, `copy`, `csv`, `errno`, `functools`, `gc`, `getopt`, `inspect`,
`itertools`, `json`, `locale`, `pickle`, `queue`, `signal`, `stat`, `types`,
`unittest`, `urllib` (request/error), `zipfile`, plus `tarfile`,
`concurrent_futures`, `token`/`tokenize`, `shelve`, `codecs`, `fileinput`,
`linecache`, `mimetypes`, `socketserver`, `smtplib`, `ftplib`, `poplib`,
`imaplib`, `http_server`, `xml_etree`, `html_parser`, `profile`, `pstats`,
`tracemalloc`, `uu`, `quopri`, `zlib`, `ssl`, `sqlite3`, `asyncio`,
`importlib`. **Per the 2026-07-15 workload addendum: do not expand this
further until the foundational items above (IR-backend-as-default, hybrid
execution, memory management, self-host correctness) are complete.**

**Notable constraints vs. CPython** (by design, not bugs): `functools.
lru_cache`/`cache`/`wraps` are pass-through stubs (no real memoization);
`copy.copy`/`deepcopy` accept `list` only in the generic form (`copy_dict`/
`deepcopy_dict` for dicts); `queue.join()` busy-spins single-threaded;
`pickle` uses a text format, not CPython's binary protocol; `unittest.main()`
doesn't auto-discover tests; `urllib.request` is HTTP-only (no TLS/ssl
runtime). **Missing entirely**: `multiprocessing`, full `xml` (beyond
`ElementTree`).

## pyinbin (separate from all of the above)

`asmpython/pyinbin/` is a from-scratch Python bytecode VM (not the native
compiler), used as a fallback so CPython stdlib / arbitrary Python source can
run without native codegen support for every language feature. Has its own
object model (`PyClass`/`PyInstance` emulate classes without real CPython
type machinery — metaclass behavior is hardcoded stubs, a recurring source
of subtle bugs). Conformance gate: `tests/cpython_conformance.py` runs real
CPython's `Lib/test/test_*` modules through pyinbin; `tests/
exhaustive_runner.py` is a generated-grammar-coverage tool using real CPython
as oracle (198/200 last confirmed, two known gaps: `except*`/exception
groups unimplemented, `type(a_module).__name__` reports the wrong name).
Extensive bug-fixing history (generator `.send()`, `super()` lexical
resolution, PEP 649 deferred annotations, try/finally exception safety,
match-statement literals) — check `devthread.txt` and recent commits before
re-deriving from scratch.

## Test suite baseline

`python -m tests.runner` — **481/489 passing** as of 2026-07-15 (commit
`1a40ee30`, after fixing the `--no-pyinbin-fallback` gap described above).
The remaining 8 failures are pre-existing and unrelated: `collections`
stdlib depth (`151_collections_module.py`, `166_ordereddict_methods.py`,
`167_counter_operators.py`, `191_collections_module.py`,
`228_collections_depth.py`, `250_collections_depth.py`,
`296_collections_namedtuple.py`) and one environment issue
(`53_dynamic_import.py`). If the count regresses toward the old, wrong
baseline (~434/459), check whether `tests/runner.py`'s `run_negative()`
still passes `--no-pyinbin-fallback` before re-diagnosing from scratch.

## Pending 2.0.0 workload (confirmed, NOT yet started)

Added to the required 2.0.0 workload 2026-07-15, to be scoped as its own
tracked effort after roadmap docs are updated:

- Finish the IR-based x86-64 backend and make it the default, fully
  replacing the legacy backend after parity.
- Run the full suite against native/pyinbin/CPython baselines; convert every
  mismatch into a regression test.
- Embed pyinbin into produced executables so native and interpreted code can
  coexist and call across a defined bridge without CPython.
- Automatic native/pyinbin partitioning, plus optional `@native`/`@dynamic`
  hints.
- `native-only`/`interpreted`/hybrid execution modes.
- Build-explanation report (why each function/module was compiled,
  interpreted, packaged, or rejected).
- HIR/MIR/LIR-style staging as the IR evolves; explicit `python`/`native`/
  `freestanding` semantic profiles.
- Real memory management: refcounting, safe ownership across calls/
  containers/exceptions, constant immortality, cycle handling.
- Deterministic fixed-point self-hosting (gen1 builds gen2, gen2/gen3
  behave equivalently).
- Backend system as a versioned public SDK: serialized IR, capability
  declarations, validation, conformance tests.
- Reproducible/hermetic builds: lockfiles, content hashes, signed package
  metadata, safe archive extraction, machine-readable build manifests.
- Keep ARM64/macOS/Pi/optimization/custom-backend work, but prioritize
  backend parity, hybrid execution, memory safety, self-host correctness
  first.

User's explicit instruction: work incrementally, preserve existing
functionality, commit coherent milestones, and do not claim compatibility or
native compilation where fallback or semantic differences remain.

## Notes for whoever resumes this

- `docs/EXTENSIONS.md` documents the newly-shipped extension system in full;
  read it before touching `extend`/`retract`/`const` or before designing a
  second built-in extension.
- A `999_comprehensive_codegen.py` test case has its `# expect:` block
  captured by actually running the file under real CPython, specifically to
  catch wrong-but-plausible expected output a human wouldn't notice. Known
  gap: `print(d.items())` prints raw pointer integers instead of formatted
  tuples.
- When investigating a self-host-only crash: `print()` calls inside
  `SemaAnalyzer`/`Codegen` methods can produce no visible output when the bug
  being chased is itself "method calls silently don't execute" — use gdb
  breakpoints/backtraces instead of prints in that situation.
- Multi-agent shared workspace: this project is sometimes worked by more than
  one agent concurrently against the same git working tree/branch (`beta`).
  Check `devthread.txt` for another agent's in-progress notes before assuming
  you have the tree to yourself.
- `codegen.py`'s `_emit_float_repr_fixup` docstring (~line 16334) has a block
  of corrupted/garbage text embedded in it (random character runs, not
  malicious, looks like an accidental paste/edit artifact) — harmless (it's
  a comment, the code below it is correct and unaffected) but worth cleaning
  up next time that function is touched.
