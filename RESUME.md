# 2.0.0 Resume

Full historical detail for everything summarized in this file is recoverable
via `git log -p -- RESUME.md`. This file is intentionally kept short — it's a
pointer to *current state*, not a full session-by-session journal.

## Directive

"Continue dev until we hit 2.0.0 ready," scoped 2026-06-18 as: garbage
collector, optimizations, selfhost-capable, ARM support, Mac support (Intel +
Apple Silicon), Raspberry Pi support (OS + bare metal). Running as an
autonomous `/loop` — see the user's `loop` skill for how to act without
supervision (continue established work, commit/push on clear continuations,
don't invent new scope, stay reversible).

See `docs/ROADMAP.md`'s 2.0.0 "Milestones" table (M0 Foundation, M1 Porting,
M2 Runtime polish, M3 Optimization tier-1, M4 Ecosystem/UX, M99 Stretch) for
milestone-level exit criteria. The numbered work order below is the
day-to-day sequencing within that structure.

**Confirmed work order** (real dependency chain, user-confirmed):

1. IR migration (see "IR Migration Status" below — **architecture changed
   mid-flight**, read that section before resuming).
2. Linear-scan register allocator.
3. New backend lowering; validate parity vs the test suite.
4. macOS Intel x86-64 target.
5. ARM64 lowering (Linux first).
6. macOS Apple Silicon.
7. Raspberry Pi Linux.
8. Raspberry Pi bare metal (freestanding ARM64 — new work).
9. Garbage collector (refcounting).
10. Optimization passes beyond the existing peephole dead-store pass.
11. **Selfhost** (asmpython compiling itself) — became the dominant focus of
    many sessions; see "Selfhost Status" below. Originally scoped
    "opportunistic, never blocking" but has absorbed most recent work because
    it's directly exercising the new backend on a large real program.
12. Stdlib completion — see "Stdlib Status" below.
13. Release pass: CODE_OF_CONDUCT/CONTRIBUTING/SECURITY/issue templates,
    CHANGELOG, version bump off `-preview`.

**Tangential, NOT active work**: user is also building **uASM**, a modular
machine-code compiler with swappable backends/frontends, currently depending
on asmpython. Plan: finish 2.0.0 first, fork asmpython into a uASM-facing
frontend afterward, as a separate effort. Don't let this influence IR/backend
design decisions above.

## IR Migration Status — architecture changed mid-flight

**Original plan (superseded)**: a from-scratch SSA IR builder, `ssa_build.py`,
built incrementally node-kind-by-node-kind alongside `ir.py`/`ir_builder.py`,
targeting a to-be-written `X86_64Target` lowering that would eventually
replace `codegen.py`'s direct-emission entirely. This got substantial
coverage (primitive arithmetic, control flow, strings, lists/dicts/sets/
tuples, comprehensions, f-strings incl. full format-spec support, global
reads/writes) before being **superseded**.

**Current approach**: `asmpython/_compiler/ir_lower.py` lowers the AST
directly to `ir.py`'s IR (a lighter-weight, more incremental strategy than
`ssa_build.py`'s ground-up rewrite), feeding a real custom backend under
`asmpython/_backends/`:

- `_backends/x86_64/`: custom encoder, register allocator (`regalloc.py`),
  phi elimination (`phi_elim.py`), COFF/ELF/PE readers and linkers
  (`coff.py`, `elf.py`, `elf_linker.py`, `pe_linker.py`) — **replacing NASM +
  gcc as the assemble/link step**, wired up via `--backend x86-64`.
- `_backends/ternary/`: an early speculative ISA backend (uASM-related).

**`ssa_build.py` no longer exists in the tree** — deleted when this pivot
happened. Its design lessons (RAW_ASM argument-register convention, "no
internal jump labels in RAW_ASM text", the two-value-kind SSA model) may
still be relevant to `ir_lower.py`/the x86-64 backend; worth checking before
assuming anything needs re-deriving from scratch.

**Known gap flagged, not yet fixed**: `ir_lower.py` has no comprehension
lowering at all (`A.Comprehension`/`A.DictComprehension` unhandled).

**Next step on resume**: reconcile this file's plan-step 1 with the actual
current architecture (this section is that reconciliation); pick up
comprehension lowering in `ir_lower.py`, then continue closing the gap to
full parity with `codegen.py` under `--backend x86-64`.

## Selfhost Status (plan-step 11)

Selfhost = asmpython (gen0, built by CPython) compiling its own source to
produce gen1, and gen1 compiling the same source again to produce gen2 (the
"ultimate test" of self-consistency). This has consumed most recent session
time because it exercises the new backend against a large real program.

**Current blocker**: gen1 compiling `asmpython/__main__.py` produces a
gen2 `.asm` truncated to ~4,426 lines (vs. gen0's ~510,000+), zero function
labels emitted, failing to assemble (`userfn_main` undefined). Not yet
root-caused — likely `program.py`'s whole-program-merge import closure
silently failing to expand for gen1 specifically. Two other known-open,
not-yet-fixed issues found along the way:
- `import os` (alone, unused) segfaults gen1 specifically (works for
  `math`/`sys`/`random`/`gc`/`io`/`re`) — narrowed to gen1's own
  self-compile-time materialization of `STDLIB_BINDINGS["os"]`, not `os.py`'s
  content (content edits don't change the crash).
- `isinstance(x, T)` in code gen1 *produces* always returns `False` for the
  *first*-declared class in a program (2nd/3rd+ classes work) — a
  wrong-output bug, not a crash. Root cause not found.

**Bugs found and fixed this arc** (chronological; each was independently
real and verified against gen0/gen1 with no regressions — full repros and
gdb traces in git history if needed):

1. `set |=` / `set` comparison operators fell back to raw pointer ops
   instead of dispatching to set-specific runtime helpers.
2. Closure free-variable type inference defaulted unannotated container
   literals to `"int"`, misreading list/dict headers.
3. `_runtime_dict_items` spilled a value into the Win64 shadow space,
   corrupting a concurrent `malloc` call.
4. `_gen_boolop`/truthiness tested raw pointer-nonzero, wrong for empty
   containers and floats (crashed every function with ≥1 parameter).
5. `driver.py` passed a list to a `subprocess.run()` stub that only accepts
   a string; a related `os.environ` opaque-attribute NULL-deref.
6. `argparse.py`'s `_convert` wrapped `None` (unset flag) in a broken `Path`
   instead of passing it through.
7. An empty-then-appended list of tuples (`reg_loads`) had no sema
   type-inference path, silently typed `"any"`, breaking int→str dispatch.
8. Same "opaque `any` treated as the wrong runtime shape" bug class via
   `set(getattr(f, "nonlocal_vars", []))`.
9. A chain of `getattr(..., default)`-returns-opaque bugs (six occurrences),
   plus `cls_def = None` reassigned in a loop never narrowing past `None`.
   Partially fixed; some of this arc's symptoms were later found (below) to
   have a different root cause (`copy.deepcopy` on AST nodes, and a
   `program.py` `key()` missing an `isinstance` arm for `FromImport`).
10. `sema.py`'s `_bind_args` called `copy.deepcopy()` on AST nodes — under
    self-hosting this resolves to asmpython's own `stdlib/copy.py`
    (list-only, wrong memory layout for a non-list object) instead of
    CPython's generic `copy`. Fixed via an explicit AST-node cloner
    (`_clone_default_expr`).
11. `parser.py`'s bytes-literal handling iterated an opaque (`object`-typed)
    `Token.value` field directly; fixed by binding to an explicit `str`
    local first.
12. Three `program.py` whole-program-merge bugs: `DictComprehension`
    assumed `Comprehension`'s multi-`for` fields; `_class_free_names` was a
    stub always returning `set()`; no equivalent origin-tracking existed for
    plain top-level functions at all (only classes). All three fixed
    (`func_origin`/`_func_free_names` added).
13. `_check_block` had no per-statement error recovery in collect-errors
    mode (only per-block) — one early unrelated `SemaError` silently
    dropped every later statement in the same block, including globals
    bug #12 had already resolved. Fixed with per-statement try/except.

**Also fixed, general hardening** (not part of the bug-number sequence):
multi-type `isinstance(x, (T1, T2))` crashes gen1 outright — every call site
across the compiler's own source was split into single-type checks as a
standing workaround (keep doing this going forward, don't reintroduce the
tuple form in compiler source); `isinstance()` on a list element is resolved
statically from the list's inferred type, not at runtime — affects
`_parse_for_target`'s target-count detection, fixed by always returning a
list; several more opaque-attribute-defaults-to-int sites patched with
explicit type casts.

**Toolchain notes**: `build/*.exe` binaries can get externally wiped
(Windows Defender quarantining unsigned freshly-built exes) — just rebuild.
Avoid output filenames containing "update"/"install"/"setup"/"patch"
(triggers UAC). Space-containing toolchain paths (`C:\Program Files\NASM`)
aren't handled by `_resolve_tool`'s `Path.is_file()` — use space-free copies
for selfhost testing.

## Stdlib Status (plan-step 12)

Most of the user's explicitly-requested module list is complete or
rewritten: `abc`, `argparse`, `array`, `base64`, `binascii`, `collections`,
`contextlib`, `copy`, `csv`, `errno`, `functools`, `gc`, `getopt`, `inspect`,
`itertools`, `json`, `locale`, `pickle`, `queue`, `signal`, `stat`, `types`,
`unittest`, `urllib` (request/error), `zipfile`. Also present: `tarfile`,
`concurrent_futures`, `token`/`tokenize`, `shelve`, `codecs`, `fileinput`,
`linecache`, `mimetypes`, `socketserver`, `smtplib`, `ftplib`, `poplib`,
`imaplib`, `http_server`, `xml_etree`, `html_parser`, `profile`, `pstats`,
`tracemalloc`, `uu`, `quopri`, `zlib`, `ssl`, `sqlite3`, `asyncio`,
`importlib`.

**Notable constraints vs. CPython** (by design, not bugs):
- `functools.lru_cache`/`cache`/`wraps` are pass-through stubs (no real
  memoization — needs a dict keyed by arbitrary argument tuples).
- `copy.copy`/`deepcopy` accept `list` only in the generic form; use
  `copy_dict`/`deepcopy_dict` for dicts (no runtime `isinstance` dispatch on
  an opaque parameter).
- `queue.join()` busy-spins in single-threaded programs.
- `pickle` uses a text format, not CPython's binary protocol.
- `unittest.main()` doesn't auto-discover tests (no reflection); build a
  `TestSuite` explicitly.
- `urllib.request` is HTTP-only (no TLS/ssl runtime).

**Missing entirely**: `multiprocessing`, full `xml` (beyond `ElementTree`).

## Notes for whoever resumes this

- The 454/455-passing test suite (`python -m tests.runner`) has one known,
  pre-existing, unrelated failure throughout this entire history — not a
  regression signal.
- A `999_comprehensive_codegen.py` test case has its `# expect:` block
  captured by actually running the file under real CPython (not
  hand-transcribed), specifically to catch wrong-but-plausible expected
  output a human wouldn't notice either. Known gap: `print(d.items())`
  prints raw pointer integers instead of formatted tuples (needs a
  tuple-element repr path in `_runtime_list_repr`).
- When investigating a self-host-only crash: `print()` calls inside
  `SemaAnalyzer`/`Codegen` methods produce no visible output when compiled
  into a selfhosted binary if the bug being chased is itself "method calls
  silently don't execute" — don't waste time debugging via prints in that
  situation; use gdb breakpoints/backtraces or debug output from a plain
  top-level function instead.
- 2026-07-06: `origin/beta` and a local session diverged significantly (81
  commits) while an autonomous session was working from an older checkout.
  That session's local-only work (a `--additional-compiler` CLI flag using
  the FFI/`STDLIB_BINDINGS` mechanism, a `pyinasmpy` stdlib module written
  in the compilable subset, and `ssa_build.py` increments now superseded by
  the `ir_lower.py` pivot above) was preserved on branch
  `backup-local-2026-07-06` (pushed to origin) rather than merged, to avoid
  corrupting actively-evolving shared state. Worth a look if
  `--additional-compiler`/`pyinasmpy` are still wanted — they'd need
  re-porting onto the current `ir_lower.py`/`_backends` architecture, not a
  straight merge.
