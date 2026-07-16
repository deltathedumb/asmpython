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

**Last confirmed status** (2026-07-16, commit `fc688dfd`): smoke-tested
`--backend x86-64` against the first 60 `tests/cases/*.py` (not part of the
automated suite, which exercises the legacy backend only): 39/60
compiled+linked (up from 34/60). Fixed along the way: a real general bug
where any top-level `for x in <list>:` wrote its loop variable to an unused
local stack slot while every read resolved it as a module global, so the
variable always read back as zero — silently corrupting any module-scope
for-loop; missing `round`/`divmod`/`hex`/`oct`/`bin`/`bool`/`repr`/`input`
builtins and a broken `pow(int,int)` (was routed through msvcrt's
double-only `pow`, corrupting output) that were previously falling through
to a blind "assume it's a real DLL symbol" call path; `print()`/`str()` only
handled str/int/float, printing lists/dicts/tuples/instances as raw pointer
values and `True`/`False`/`None` as `1`/`0`/`None`-via-wrong-path — both now
delegate to `_lower_expr_as_str`, the repr helper f-strings already used.
Spot-checked full build+run+expected-output byte-for-byte match on the
newly-fixed cases (101, 106, 108, 127) — all exact. `tests.runner` still
481/489, no regressions.

Remaining 1-60 failures are a mix of: float support (list/dict/tuple float
elements, float binops `%`/`**`, float params — `'RegLoc' object has no
attribute 'offset'` on `120_float_params.py`/`121_float_instance_attrs.py`
looks like a distinct regalloc bug worth its own investigation), several
unimplemented `MethodCall`s (`list.sort`, `dict.pop`, `str.rpartition`/
`casefold`/`format`), `del` statement, starred/non-Name tuple-assign
targets, walrus operator, `sorted(key=...)` lambda body, and two
undefined-symbol gaps (`trunc`, `Dog__greeting` — the latter smells like a
property/method-resolution bug, not a missing libm entry).

**Next step on resume**: triage the remaining backend-parity smoke-test
failures (one symbol-table fix or one missing `ir_lower.py` codegen case at a
time) toward full parity with `codegen.py` under `--backend x86-64` — this is
the direct predecessor of the newly-confirmed "make the IR-based backend the
default" workload item. Suggest starting with `Dog__greeting` (property
resolution) since it may be a quick, high-value fix, then the float-params
`RegLoc.offset` crash since float support blocks several other cases.

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
