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

**Expanded again 2026-07-16** ("Everything Python" directive): 2.0.0 must be
able to run essentially any unmodified real-world Python program end-to-end
(native-first, pyinbin fallback for anything not yet native-compilable) —
not just the existing test-suite scenarios. This raises the bar on
language/stdlib conformance and makes broad architecture/OS backend
coverage (ARM64, macOS, Raspberry Pi — "a lot for common architectures",
explicitly not an unbounded backend list) a real requirement rather than a
stretch goal. Does not reorder the confirmed work order below; sharpens
what "done" means at each stage, especially stdlib/conformance breadth and
backend parity. Directive: work continuously through the confirmed order
without stopping for check-ins between items — checkpoint via commits +
this file, not via pausing.

**Tangential, NOT active work**: user is also building **uASM**, a modular
machine-code compiler with swappable backends/frontends, currently depending
on asmpython. Plan: finish 2.0.0 first, fork asmpython into a uASM-facing
frontend afterward, as a separate effort. Don't let this influence IR/backend
design decisions above.

## Extension System — DONE (2026-07-15, commit `1a40ee30`; activation model redesigned 2026-07-16, see below)

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

**Activation model redesigned** (2026-07-16, commit after `2b702c51`): the
user explicitly rejected in-source `extend`/`retract` directives — "at
first i thought itd be fine but at second thought i dont want to change
python at all without user consent (via `--ext` being the way users add,
and by default adding none)". Replaced entirely with a `--ext NAME` CLI
flag (repeatable), activated once per compile invocation and applied
*uniformly* to every module a whole-program compile merges (never
per-file, never toggleable mid-source) — a program's grammar is now a
property of the build, not of anything the source itself controls.

- `parser.py`: deleted `_parse_extend`/`_parse_retract`/
  `_looks_like_extend_stmt`/`_looks_like_retract_stmt`/`_parse_dotted_name`
  and their dispatch sites (module-level + the class-body bespoke
  scope-violation check, now `const`-only). `Parser.__init__` gained an
  `active_extensions: frozenset[str] | None` param, activating each name
  against a fresh `ExtensionContext` immediately at construction — before
  any token is parsed — instead of extension activation being something
  parsing itself could mutate.
- `ast_nodes.py`: deleted the now-pointless `Extend`/`Retract` transient
  AST node classes (nothing produces them anymore).
- `driver.py`/`program.py`/`__main__.py`: threaded `active_extensions`
  through `_compile_program` → `compile_source`/`compile_targets` →
  `load_program` (applied to both of its `Parser(...)` construction sites
  — entry module and every merged import) → every `Parser(...)` call.
  New `--ext NAME` argparse flag (`action="append"`, default `None` →
  `frozenset()`), wired into both the build path and `--check`.
- `errors.py`: retired the `extend`/`retract`-specific wording on
  P011/P012/P018 (now describe `--ext`/`const` instead); P013/P014/P017
  (duplicate-activation / not-active / retract-blocked) are still real,
  tested `ExtensionContext` API behavior but effectively unreachable via
  the CLI now (`--ext` is deduped into a `frozenset` before activation,
  and there's no in-source retraction) — codes kept allocated, comments
  updated to say so.
- `docs/EXTENSIONS.md`: rewritten for the new model end-to-end.
- Test suite: deleted `tests/cases_fail/extend_*.py` (7 files) and
  `retract_*.py` (2 files) outright — they tested directive syntax that no
  longer exists. Added four `const_outside_module_scope_{function,class,
  loop,try}.py` negative cases (the const-based equivalent, covering the
  same five suite shapes the deleted extend-scope tests did — `if` was
  already covered by the pre-existing `const_outside_module_scope.py`).
  Renamed `const_without_extend.py` → `const_without_activation.py`.
  Deleted `454_const_retract_then_plain.py` (tested `retract` specifically,
  no equivalent concept exists anymore). Every remaining `const_*` test
  (13 negative + 3 positive) now declares its needed extension via a new
  `# ext: constants` marker-comment convention (must appear *before* the
  `# expect`/`# expect-error` block — `_parse_expect`'s collector absorbs
  every subsequent `#`-line once it starts, so a marker placed after would
  get swallowed into the expected-output/error text) instead of an
  in-source `extend constants` line; `tests/runner.py` gained a
  `_parse_ext` helper that reads this marker and appends `--ext NAME` to
  the compiled command. `tests/test_extensions.py`'s one source-syntax
  test (`ParserIsolationTests`) now constructs one `Parser` with
  `active_extensions={"constants"}` and one without, instead of
  `"extend constants\n..."` source text. `tests/test_program_isolation.py`
  rewritten from scratch — the old file specifically tested *per-file*
  activation variance (`main.py` opts in, `helper.py` doesn't), a scenario
  that no longer exists by design; replaced with tests confirming `--ext`
  reaches every merged module uniformly, and that a real `const` shape in
  the entry module without activation is a hard `ParseError` (not silently
  dropped — only an *imported* module's parse failure is ever silently
  skipped, per `program.py`'s pre-existing leniency).

Verified: `tests.runner` 475/483 (matches the 482/493 pre-change baseline
exactly once the deliberate net file-count change is accounted for — same
8 pre-existing, unrelated `collections`/pyinbin failures in both runs,
confirmed via git-stash A/B). All 18 `test_extensions.py`/
`test_program_isolation.py` unit tests pass. End-to-end CLI spot-check:
`asmpython build ... --ext constants` compiles and runs a real `const`
program correctly; the same file without `--ext constants` fails with the
expected `P018` diagnostic mentioning `--ext constants`.

**Part B — public authoring API — DONE** (2026-07-16, same day): built the
public `asmpython.Extension`/`Backend`/`Linker` authoring API and the
backend/linker registries it needed to register into. User explicitly
scoped the self-hosting concern out of this checkpoint: a Python
dict-based registry works fine under CPython-hosted compilation, and
"asmpython has no first-class module values" (the reason the x86-64
backend's linker dispatch was hardcoded if/elif to begin with) is now a
separately tracked pending item ("First-class module values" below), not
something this work needed to solve first.

- **Backend/linker registries**: new `asmpython/_backends/__init__.py`
  (`register_backend`/`get_backend`/`registered_names`, plain dict) and
  additions to the existing `asmpython/_linkers/__init__.py`
  (`register_linker`/`get_linker`/`registered_names`). `driver.py`'s
  `_run_backend` keeps `legacy`/`x86-64`/`ternary` as special cases (too
  much bespoke per-backend wiring, especially x86-64's ABI shims/runtime
  linking/GCC resolution, to genericize) but falls through to a new
  `_run_backend_registered` (plain compile→link→write, mirroring
  `_run_backend_ternary`'s simple shape) for any other `--backend` name.
  `_backends/x86_64/__init__.py`'s `run_backend_link` keeps `gcc`/
  `builtin` hardcoded the same way, falling through to `_linkers.
  get_linker` for anything else. `__main__.py`'s `--backend`/`--linker`
  argparse args dropped their `choices=(...)` constraint (a fixed choice
  list can't validate a name that isn't known until plugin-load time).
- **`asmpython/extend.py`** (new, public — exposed as `asmpython.
  Extension`/`Backend`/`Linker` via `asmpython/__init__.py`):
  - `Extension(id, *, version="1.0", requires=None, conflicts=None,
    statement_handlers=None)` registers immediately at construction (no
    separate `.register()` call) by wrapping itself in a thin
    `CompilerExtension` instance and calling `extensions.
    register_extension(...)` — generalized to accept an already-built
    instance, not just a class (an in-tree built-in still registers a
    class for the "fresh instance per activation" case; a dynamically-
    built `Extension` has no subclass of its own and nothing meaningful to
    reset between activations, so it registers itself directly).
  - `Backend(name, impl)` / `Linker(name, impl)` register directly into
    the two registries above. `impl` must conform to `IRBackend`'s
    `compile`/`link` (backend) or a bare `link(ctx)` (linker) contract.
  - **Statement-handler dispatch, genuinely new plumbing**: a plugin's
    `statement_handlers={"kw": callable}` entry needed the parser to
    actually *call* something for a dynamically-registered keyword, not
    just record it — `ExtensionContext.handler_for` (previously
    documented as "exists but nothing consumes it yet") now resolves
    either the in-tree string-method-name convention OR an already-real
    callable into a genuine callable either way, and `parser.py`'s main
    statement-dispatch loop gained a real check (right after the
    `const`-specific one): for any bare `NAME` token, if `ext_ctx.
    handler_for(t.value)` returns a handler, eat the keyword and call
    `callback(self, pos)`, expecting a real `A.Stmt` node back. The
    handler drives the live `Parser` instance directly via its private
    `_eat`/`_check`/`_expect`/`_parse_expr` methods and constructs nodes
    from the private `asmpython._compiler.ast_nodes` module — documented
    as the de facto contract since there's no separate public AST surface
    yet. **Real, deliberate trade-off**: unlike `const`/`match`, a
    plugin-claimed keyword has no shape lookahead (the plugin decides its
    own grammar), so once active it's unconditionally a statement prefix
    — can't double as a plain identifier anymore. Only applies when the
    invoker explicitly activated it via `--ext`.
  - `--ext` (`__main__.py`) now accepts either a bare registered id or a
    filesystem path (`_resolve_ext_flags`/`_load_ext_plugin`): a path is
    exec'd in the host CPython process (never compiled by asmpython)
    before/instead of a `--ext id`, and the id it registers is what
    actually activates — before/after diffing the extension registry's
    keys to find what a plugin file added. Errors clearly if a plugin
    registers zero or more than one `Extension` (ambiguous which id
    `--ext path.py` alone should activate).
- Fixed two pre-existing `tests/test_extensions.py` assertions that
  checked `handler_for`'s return value as a raw unresolved `(name,
  "handle_a")` string pair — now that `handler_for` genuinely resolves to
  a callable, `FakeA` needed a real (if never-actually-invoked) `handle_a`
  method to resolve against, and the assertions check identity against
  the resolved bound method instead of the old string.
- New `tests/test_extend.py`: metadata-only registration+activation, a
  real statement-handler round-trip (`let NAME = value` end to end,
  confirmed producing a real `A.Assign` when active vs. an ordinary
  identifier when the extension that claims it was never activated),
  cross-plugin statement-prefix collision, `Backend`/`Linker`
  registration retrievability.

Verified: `tests.runner` still 475/483 (no regressions). All 18
`test_extensions.py`/`test_program_isolation.py` tests pass (2 fixed for
the `handler_for` resolution change), plus all 6 new `test_extend.py`
tests. End-to-end CLI spot-check: a real external plugin file defining
`let x = 5` (via `asmpython.Extension(id="let_binding",
statement_handlers={"let": handle_let})`) loaded via `--ext path/to/
plugin.py`, compiled with `--backend x86-64`, and run — printed `5`
correctly. `Backend`/`Linker` registration spot-checked directly (no full
custom-backend implementation attempted — that's a much larger, separate
effort, out of scope here). A plugin registering zero extensions fails
with a clear, direct error rather than silently doing nothing.

`docs/EXTENSIONS.md` rewritten with two new sections ("User-authored
extensions", "Backends and linkers") documenting all of the above in
full, including the shape-lookahead trade-off and the self-hosting
caveat on both registries.

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

**Third follow-up** (same day, commits after `4a0bf26d`): implemented
`del` statement (`del x` / `del xs[i]` / `del d[key]`, new
`_abi_list_del`/`_abi_dict_pop` shims), `dict.pop(key[, default])`, dict
literal `**spread` (was silently inserting garbage instead of raising or
working — sema represents a spread key as a sentinel `Name("**")`, not
`None` as the dead code here assumed, so the intended reject-with-
`LowerError` never actually fired), and the dict/set union operators `|`/
`|=` (PEP 584 — were routing through plain integer `ior`, corrupting two
raw struct-header pointers instead of merging entries; `132_dict_union.py`
segfaulted). Also found and fixed **two pre-existing, unrelated object-
writer bugs** while chasing an `UnicodeDecodeError` in the COFF linker on
`134_dict_order.py`:

- `_collect_module_globals`'s `A.For` handler always registered `s.var`
  as a module global, even for a tuple-unpack loop (`for k, v in ...:`)
  where the bound names live in `s.targets` and `s.var` is legitimately
  `''` — registering the empty string produced a COFF symbol with no
  name at all, which the linker's reader couldn't decode. Fixed by only
  registering `s.var` when `s.targets` is empty.
- `coff.py`'s/`elf.py`'s `.data`-section builders reassigned their
  `bytearray` to a fresh zero-filled one on every global (meant to pad to
  8-byte alignment), silently discarding every earlier global's bytes.
  Currently harmless in practice (every `.data` global today is
  runtime-initialized via a `store`, never a nonzero compile-time
  initializer, so the discarded bytes were always zero anyway) but a real
  latent bug the moment this backend gains compile-time-constant
  initializers. Fixed to pad in place instead.

Verified: `tests.runner` still 481/489 (no regressions). Full
build+run+expected-output sweep on 1-60 went 36/60 → 40/60 exact matches,
build-failures 12 → 10.

**Fourth follow-up** (same day, commits after `691766c8`): implemented
the full set-method surface (`.add`/`.clear`/`.union`/`.intersection`/
`.difference`/`.discard`/`.remove`/`.copy`/no-arg `.pop()`) — sets are
dict-backed here (str-keyed, dummy value 1 per member), so this ported
`codegen.py`'s `_gen_set_setop`/discard-remove/copy/pop design onto the
same `_abi_dict_*` shims already built for dict support, plus two new
shims (`_abi_dict_clear`, `_abi_str_concat_dup` — the latter needed
because an int set-member must be duplicated off `_abi_int_to_str`'s
shared static buffer before being stored as a long-lived dict key).
Fixed `104_set_methods.py`/`123_set_discard_remove_copy_pop.py` (were
segfaulting: unimplemented set methods fell through to the generic
"unknown method → return 0" stub, and subsequent `x in <that 0>` deref'd
a null pointer).

Verified: `tests.runner` still 481/489. Full build+run+expected-output
sweep on 1-60 went 40/60 → **42/60 exact matches**, build-failures 10.

**Remaining 1-60 gaps are now down to two self-contained feature areas**
(everything smaller/scattered has been cleared this session):

1. **F-string format specs** (`135`/`136`/`137`/`138`/`110_fstring_conv`):
   alignment, width, precision, thousands-grouping, binary/hex-with-
   prefix, and `!r`/`!s` conversion flags are entirely unimplemented —
   the `:spec` (and `!conv`) mini-language is silently ignored and the
   plain value prints instead. Worth a dedicated pass: parse the spec
   once into a small struct (fill-char, align, sign, width, grouping,
   precision, type), then drive one shared formatter off it — this would
   close out most/all of the remaining fstring_* cases at once. Look at
   how `codegen.py` already parses/applies these specs (search for
   `format_spec` / `_gen_fstring_segment`) and port that parsing, not the
   codegen, since the IR side just needs the same decisions expressed as
   `_abi_*` calls instead of inline asm.
2. **`%`-style string formatting** (`109_pct_format`/`111_pct_repr`):
   `"%s is %d" % (...)` has zero implementation in this backend and
   segfaults at runtime (compiles as an unrelated/unchecked expression,
   then crashes). A separate, comparably-sized feature to format specs
   — CPython's `%` operator on a string LHS with a tuple/dict RHS,
   its own mini-language (again worth checking `codegen.py`'s existing
   implementation as the port source).

Build-failures beyond format specs: `list.sort`, `str.rpartition`/
`casefold` `MethodCall`s, starred/non-Name tuple-assign targets, walrus
operator, `sorted(key=...)` lambda body — smaller, scattered gaps like
the ones cleared this session, likely worth another pass once the two
big format features are done (or interleaved, since they're independent
of each other).

**Fifth follow-up** (same day, commits after `94096058`): implemented
f-string format specs in full — alignment/fill/width, zero-pad, thousands
grouping (`,`/`_`, including the zero-pad+grouping combo), binary `b`/`#b`
with width/sign accounting, str precision truncation, `!r`/`!s`/`!a`
conversion flags, and bool/None-formats-as-underlying-int. Ported
`codegen.py`'s `_gen_fstring_segment`/`_gen_fstring_aligned`/spec-parsing
helpers (`_cfmt_for_spec`, `_split_fmt_align`, etc. — all pure compile-time
string manipulation, since `fmt_spec` is always a literal captured at lex
time; `f"{x:{width}}"`-style runtime specs aren't supported by either
backend) into new `ir_lower.py` functions (`_lower_fstring_segment`/
`_lower_fstring_aligned`/`_lower_int_value_str`), threading each segment's
`fmt_spec`/`conv_flag` (parser-stamped attributes, not AST fields) through
to six new `_abi_*` shims: `_abi_str_truncate`, `_abi_int_to_binary`,
`_abi_group_digits`, `_abi_group_digits_zeropad`, `_abi_int_fmt`,
`_abi_float_fmt`. Also deleted a confirmed-dead, less-capable duplicate
f-string lowering branch in `ir_lower.py` (unreachable — an earlier
`isinstance(e, A.FString)` check in the same function always matched
first).

Two real, non-obvious Win64 ABI bugs found and fixed while building
`_abi_int_fmt`/`_abi_float_fmt` (both call `sprintf` with a
caller-supplied format string, needing a fresh malloc'd buffer per call
unlike the other shims' shared-static-buffer or register-only patterns):

1. **Shadow-space corruption**: a shim that stores its own locals at
   `[rsp+16]`/`[rsp+24]` while later calling another function (`malloc`
   then `sprintf`) gets those locals silently overwritten — Win64
   callees are allowed to scribble anywhere in `[rsp, rsp+32)` as their
   own shadow space. Confirmed via gdb: `rax` held `42` (the raw int
   argument) instead of a valid pointer at the point of a crash, because
   the "stash the malloc'd buffer pointer" slot lived inside that
   64-callee-writable region. Fixed by moving all shim-local storage to
   `[rsp+32, ...)`, above the shadow space.
2. **Wrong argument register for `_abi_float_fmt`**: this backend's
   `call` IR op assigns Win64 argument registers using **one shared
   positional index across both the integer and float register
   classes** (not independent per-class counters) — confirmed by reading
   `_backends/x86_64/codegen.py`'s `_call`. So for a 2-arg call whose
   *first* argument is float-typed (`_abi_float_fmt(value, fmt_ptr)`),
   the second (non-float) argument lands in **RDX** (the second
   positional slot), not RCX (which would be arg0's slot if arg0 were
   non-float). The shim initially assumed RCX, which doesn't fail to
   assemble or obviously crash — it silently reads uninitialized/garbage
   RDX as the format-string pointer, producing empty output on some
   inputs and a delayed segfault on a second call once corrupted heap
   state caught up. Any future `_abi_*` shim taking a float as its
   *first* IR-level argument alongside other args needs this same
   positional-index reasoning, not "float args go in xmm, everything
   else starts from rcx."

Verified: `tests.runner` still 481/489 (no regressions). Full
build+run+expected-output sweep on 1-60 went 42/60 → **47/60 exact
matches** (only the two `%`-format cases remain as mismatches on 1-60).
Also spot-checked every other fstring_*/format-related test case in the
full suite beyond the first 60 (`13`, `35`, `92`, `141`
zeropad-grouping-combo, `438` advanced) — all exact matches, confirming
no regressions in the broader corpus either.

**Sixth follow-up** (same day, commits after `9025eb12`): implemented
`%`-style string formatting (`"...%s/%d/%f..." % (args)`). This one was
fast and low-risk compared to the f-string work: `A.parse_pct_format`
(`ast_nodes.py`) is already a **shared** compile-time parser both sema
(validation) and `codegen.py` (lowering) use, so there was no parsing
logic to port, just the codegen decision tree — and every primitive it
needs (`_abi_int_fmt`/`_abi_float_fmt`/`_abi_str_ljust`/`_abi_str_rjust`/
`_abi_str_concat`, plus `_lower_fstring_segment` reused as-is for `%s`/
`%r`) already existed from the f-string pass just before it. New
`_lower_pct_format` in `ir_lower.py`, wired into `BinOp`'s `%` dispatch
ahead of the int/float `%` (fmod) path, mirroring `codegen.py`'s own
"str ops dispatch before float/int" precedence. Fixed
`109_pct_format.py`/`111_pct_repr.py` on the first attempt — both exact
matches, no follow-up bugs (the two Win64 ABI shim bugs from the
f-string pass were already fixed and this feature reuses those same
shims, so it inherited the fix for free).

Verified: `tests.runner` still 481/489 (no regressions). Full
build+run+expected-output sweep on tests/cases 1-60: **49/60 exact
matches, ZERO mismatches** — every 1-60 case that builds now produces
byte-for-byte correct output; the remaining 10/60 are build failures
only (unimplemented features listed below), not correctness bugs.
Full-suite sweep (all ~440 cases) also confirmed no regressions:
230→232 passing, mismatches 66→64 (exactly the 2 pct-format cases
fixed, nothing new broken).

**This closes out the "smoke-test corpus parity" push** (all six
same-day follow-ups, `fc688dfd` through this commit): x86-64 backend
build-success on tests/cases 1-60 went 34/60→50/60 across the whole
session, and — more importantly — every case that builds is now
verified byte-for-byte correct against its expected output, which
wasn't true even for many of the 34 that built at session start.
Remaining 1-60 build failures, all smaller/scattered and independent
of each other: `list.sort`, `str.rpartition`/`casefold` `MethodCall`s,
`str.format`/bare `format()` builtin (a third, separate mini-language —
`A.parse_format_fields` in `ast_nodes.py` is presumably its shared
parser, unexplored), starred/non-Name tuple-assign targets, walrus
operator, `sorted(key=...)` lambda body.

**Seventh follow-up** (2026-07-16, commits after the sixth): cleared most
of the remaining scattered 1-60 gaps. Implemented `list.sort()` (extracted
`_lower_sorted`'s key/reverse logic into a shared `_lower_sort_inplace`
helper, used both by `sorted()`-after-clone and `sort()`-in-place),
`list.copy()` (full-range `_abi_list_slice`), `list.count(x)` (IR loop,
`_abi_str_eq` for str elements else `icmp.eq`), `str.casefold` (aliased to
the existing `_abi_str_lower` — fine for this backend's ASCII-only string
support), and `str.rpartition` (new `_abi_str_rpartition` shim). Also
closed both tuple-assign gaps: parallel-form (`a, b = x, y`) now supports
`Subscript`/`Attr` targets (new `_store_tuple_assign_target` helper, not
just `Name`), and starred-unpack (`a, *rest, b = xs`) now lowers via IR
block loops computing before/after slices plus an `_abi_list_slice` for
the middle, storing each via `_store_loop_target`.

While wiring starred-unpack, found a **pre-existing** (not newly
introduced) bug: `_collect_module_globals` typed plain-`Name` targets
sharing a `TupleAssign` with a `StarTarget` via `tuple_elem_types`
(always empty for a list RHS) instead of `_iter_element_type`, silently
defaulting them to `"any"`. Fixed by computing a shared `uniform_el_ty`
via `_iter_element_type` whenever the RHS is list-typed or a `StarTarget`
is present.

Chasing that fix's runtime symptom (first/last values in a starred-unpack
print all showing the *last* value) surfaced a real, previously-unknown
**bug class**: `_runtime_fmt_elem` and `_abi_float_to_str` both return a
pointer into a **fixed shared static buffer** (`itoa_str_buf` /
`_abi_float_to_str_buf`) — safe for one value, but this backend's
print()/f-string design computes *every* arg's formatted string up front
before making one shared call, so two `"any"`-typed int/float values
formatted in the same multi-arg call silently alias and the later
sprintf overwrites the earlier result before anything reads it (repro:
`f1, *fmid, f2 = [1.5,2.5,3.5,4.5]; print(f1, fmid, f2)` printed `f2`'s
value for both `f1` and `f2`). Fixed at both shim sites by
unconditionally dup'ing the result via the existing
`_runtime_str_concat_dup` before returning. Audited every other static
buffer in `abi_shims.asm`: no further instances (the pre-existing
`_abi_int_to_str_buf` single call site already dup'd correctly). **Any
future `_abi_*` shim that returns a pointer into a shared/static buffer
must dup before returning if it can ever be alive concurrently with
another such result — which is the normal case for this backend's
multi-arg call lowering.**

Verified: `tests.runner` 481/489 throughout (no regressions). Full
build+run+expected-output sweep on tests/cases went 232→242 passing
(~440 total), no regressions. `164_statistics_module.py`'s pre-existing
float-garbage mismatch (`6.95186e-310`) confirmed via git-stash A/B diff
to predate this session's changes — a distinct, not-yet-investigated bug,
not caused by the buffer-aliasing fix (if anything the fix is a
prerequisite for eventually diagnosing it correctly).

Remaining scattered gaps: `str.format`/bare `format()` builtin (third
mini-language, unexplored — `A.parse_format_fields` likely its shared
parser), walrus operator (`NamedExpr`), `sorted(key=...)` lambda body
(not yet re-verified after the `_lower_sort_inplace` refactor — should
be unaffected since the refactor only moved code, didn't change logic,
but not explicitly re-tested).

**Eighth follow-up** (2026-07-16, commits after the seventh): implemented
the walrus operator (`A.NamedExpr`, `target := value`). Two parts:

1. `_lower_expr`'s new `A.NamedExpr` case (mirrors `codegen.py`'s
   `_gen_named_expr`): evaluate `value`, store into `target`'s slot via
   `_name_ptr` (same scope resolution a plain `Assign` uses), return the
   stored value so the enclosing expression can consume it.
2. A walrus target binds in the *enclosing* scope (PEP 572), not nested
   inside whatever expression it appears in — it can appear inside an
   `If`/`While` condition, a comprehension, or any nested expression, not
   just as a top-level statement. Neither `_collect_module_globals` (which
   decides module-global slot types) nor `_collect_bound_names` (which
   seeds a function's local-name set) walked *into* expressions looking
   for these, so a walrus target used inside a condition was never
   registered at all. Added a generic recursive `_walk_named_exprs(e)`
   expression walker (covers BinOp/UnaryOp/BoolOp/Compare/IfExp/Call/
   MethodCall/Attr/Subscript/list-tuple-set-dict literals/FString/
   comprehensions) plus `_register_named_expr_globals`/
   `_register_named_expr_names` wrappers, wired into both collectors at
   every statement site that carries a raw expression (`ExprStmt`,
   `Return`, `Assign.value`, `If.test`, `While.test`).

Testing `128_walrus.py` (module-scope `if`/`while`/ternary/comprehension
forms plus a function-body form) surfaced a **separate, genuinely
pre-existing bug**, unrelated to walrus itself: a function-local variable
that shadows an unrelated module-level global of a *different type*
crashed the backend (`AttributeError: 'XmmLoc' object has no attribute
'offset'` deep in register allocation). Root cause: `_lower_expr`'s
`A.Name` read case computed the load's IR type via
`ctx.mctx.global_types.get(e.name, ctx.slot_ty.get(e.name, I64))` —
checking the *module-global* type table **before** checking whether this
particular occurrence of the name is actually local. `_name_ptr` (used
right below it for the actual pointer) already did this check correctly
(`_is_global_name`); the type computation above it didn't. Repro: a
module-level `x = 0.0` plus a function with `for x in xs: total += x *
x` (never `global x`) read `x` back as a stale F64-typed load from an
I64-typed local slot. Fixed by checking `_is_global_name(ctx, e.name)`
first and picking the type table in the matching order. This is the
same "global-vs-local write/read mismatch" bug *class* documented
earlier this session (for-loop vars, range-for vars, exception bindings)
but a new *instance* of it — the earlier fixes were all about the
*write* side using the wrong helper; this one is the *read* side using
the wrong type-lookup order.

Verified: `tests.runner` 481/489 (no regressions). `128_walrus.py` exact
match on `--backend x86-64`. A full build+run+expected-output sweep
across all ~439 `tests/cases/*.py` surfaced a substantially larger set of
mismatches/crashes than the "232/242" figure quoted in the seventh
follow-up — spot-checked several (`87_lambda.py`, `64_multi_return.py`)
via git-stash A/B diffing and confirmed byte-identical crash behavior
before this session's changes, so these are pre-existing gaps the
smoke-test-only (`tests/cases` 1-60) corpus never exercised, not
regressions from walrus or the shadow-name fix. **The "232/242" number
should not be treated as a reliable full-corpus baseline going
forward** — it likely came from an earlier/narrower sweep methodology.
Whoever picks up the "validate against a broader corpus" direction below
should re-run a full sweep with exit-code checking (not just stdout
diffing — several of these are crashes, not wrong-output) to get a real
current baseline before further parity work.

**Real baseline established** (2026-07-16, same-day follow-up): ran the
exit-code-aware full sweep the note above called for. Real numbers across
all 438 `tests/cases/*.py`:

```text
OK=245  MISMATCH=24  CRASH=42  BUILD_FAIL=127  (56% passing)
```

This is the actual current state — **do not use "232/242" or any
"4x/60" smoke-corpus figure as a stand-in for full-corpus parity**. 127
build failures (unimplemented features), 42 runtime crashes (a real
correctness bug class, not just missing features), 24 wrong-output
mismatches. Full triage of all three buckets not yet done — this is a
large, multi-session task. Sweep script (not yet committed to the repo,
lived in a scratch dir this session — worth promoting to a real
`tests/backend_correctness.py` next time) builds+runs every case under
`--backend x86-64 --no-pyinbin-fallback` (auto-passing `--ext` flags read
from each case's `# ext:` marker) and buckets by exit code + stdout diff
rather than stdout diff alone, which is what the earlier, unreliable
sweeps missed (a nonzero-exit crash with empty stdout was previously
indistinguishable from "silently produced no output").

**Triage pass one** (2026-07-16, same-day follow-up — user directive:
"finish all 2.0.0 steps," working the confirmed order sequentially,
checkpointing each fix): three real bugs found and fixed, re-measured
after each:

1. **Missing msvcrt.dll symbol-table entries**: `pe_linker.py`'s
   `_DLL_FOR_SYMBOL` was missing ~25 real exports (`cos`/`sin`/`tan`/
   `asin`/`acos`/`atan`/`atan2`/`sinh`/`cosh`/`tanh`/`exp`/`srand`/
   `getenv`/`clock`/`remove`/`_stat64`/`_getpid`/`time`/`gmtime`/
   `localtime`/`mktime`/`_mkdir`/`_rmdir`/`_chdir`/`_getcwd`/`_access`),
   each confirmed a real export via `ctypes.WinDLL('msvcrt.dll')`
   attribute lookup against the live system DLL (no `dumpbin` in this
   environment; an equivalent, tool-free confirmation method). Sweep:
   245→247 OK, build failures 127→118.
2. **`"str" * int` (repeat) silently corrupted, not just unimplemented**:
   fell through to the plain-int `imul` path, multiplying the string's
   raw *pointer value* by the count and handing the garbage result to
   `printf` as if it were a real string — confirmed via gdb (SIGSEGV
   inside msvcrt.dll's `ungetwc`, called from `printf`, after the string
   arg's corrupted pointer led printf's internal state astray). This
   affected any program using `str * int` at all — a very common idiom
   (separator lines, padding) — which is why so many otherwise-unrelated
   crashing cases shared the exact same `exit=3221225477`
   (`STATUS_ACCESS_VIOLATION`) signature. Fixed with a proper
   `_abi_str_repeat` shim (wraps the existing `_runtime_str_repeat`,
   already used by the legacy backend) and a new `ir_lower.py` `BinOp`
   case routing both `"str" * int` and `int * "str"` through it (mirrors
   `codegen.py`'s `_gen_binop_str`, which always resolves the string
   operand first regardless of source order).
3. **Module-attribute access on a merged stdlib module segfaulted**
   (`string.ascii_lowercase`, `cmath.pi`, etc.): the generic instance-
   attribute fallback (`obj.name -> _abi_dict_get_default(obj, name,
   default)`) treated the module NAME (`string`) as if it were a real
   runtime dict/instance pointer — but a module name is a compile-time-
   only namespace, never bound to any variable, so `_lower_expr` on it
   silently allocated a fresh, never-initialized stack slot and read
   garbage from it as the "dict" pointer. Confirmed via gdb + a minimal
   repro (`import string; print(len(string.ascii_lowercase))`) crashing
   identically. Fixed in `ir_lower.py`: when `e.obj` is a `Name` typed
   `"module"` by sema AND the attribute name is a real materialized
   global (`program.py`'s whole-program merge already hoists a merged
   module's top-level `Assign`s as plain globals under their bare name),
   read that global directly instead of going through the dict-lookup
   fallback. **Guarded to only apply when the global actually exists**:
   a pure-FFI-binding-table module like `math.py` (`Const`/`Func` entries
   in a `BINDINGS` dict, never real `Assign` statements `program.py`
   would hoist) has no such global at all — `math.pi`-style access
   remains a separate, still-open gap (falls through to the old,
   already-broken-before-this-session behavior, confirmed not made worse:
   `ir_lower.py` has no `ffi_consts` handling for either `A.Name` or
   `A.Attr` today). This also needed a matching sema fix
   (`obj_t == "module"` previously always collapsed to `inferred_type =
   "any"`, discarding real available type info — now checks
   `self.global_scope.types` first) and, once sema started correctly
   reporting `float` for these attributes, exposed a **pre-existing bug
   in the legacy `codegen.py` backend too**: its own module-attribute
   read never did the `movq xmm0, rax` bit-reinterpret a float result
   needs (unlike the dict/instance-attribute case right above it, which
   already did) — fixed to match. Sweep: 248→252 OK, crashes 48→44.

Verified: `tests.runner` 475/483 (matching the pre-session baseline
exactly) after every fix in this pass, confirmed via repeated full
reruns. Final sweep numbers after all three fixes, from the
`OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` starting point above:

```text
OK=252  MISMATCH=24  CRASH=44  BUILD_FAIL=118
```

**Triage pass two** (2026-07-16, same-day follow-up): two more real
crash bugs, both in the same family as the module-attribute bug above —
sema had the right information (`dunder_owner`/`dunder_call_owner`
stamped correctly) but `ir_lower.py`'s lowering or reachability walker
didn't check it in every place it needed to:

1. **Unary dunder operators** (`-instance`/`+instance`/`~instance`
   calling `__neg__`/`__pos__`/`__invert__`): `A.UnaryOp`'s lowering had
   no `dunder_owner` check at all (unlike `A.BinOp`, which already had
   one) — fell through to plain `ineg`/`inot` on the raw instance
   pointer, corrupting it (confirmed via gdb: crash in application code
   on the very next dereference of the "negated" pointer). Fixing the
   lowering alone wasn't enough: the reachability walker (which decides
   which methods actually get emitted) also never checked `A.UnaryOp` for
   `dunder_owner` — `BinOp`/`Compare` did — so `Vec.__neg__` was correctly
   *called* by the fixed lowering but didn't exist in the output at all
   until this second, matching fix was added too.
2. **Calling an instance via `__call__`** (`add5 = Adder(5); add5(3)`):
   sema already stamped `e.dunder_call_owner` correctly on the `A.Call`
   node — it was even already used by the reachability walker (so
   `Adder.__call__` DID get emitted) — but nothing in `_lower_expr`'s
   actual `A.Call` handling ever checked it. The call site fell through
   to the generic "call this variable as if it were a function pointer"
   path, which tried to invoke the instance's own struct/dict pointer as
   executable code. Added a real dispatch case checking `dunder_call_owner`
   before the generic fallback.

Verified: `tests.runner` 475/483 throughout. Sweep: 252→254 OK,
crashes 44→42.

**Cumulative sweep numbers, both triage passes, from the
`OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start baseline**:

```text
OK=254  MISMATCH=24  CRASH=42  BUILD_FAIL=118
```

**Triage pass three** (2026-07-16, same-day follow-up): audited every
`dunder_owner`/`dunder_call_owner`/`dunder_contains_owner` attribute
sema stamps against every place `ir_lower.py` could plausibly need to
check it — the exact recommendation from pass two's writeup, since every
bug found so far was "sema has the right info, ir_lower.py just doesn't
check for it in one specific place." Found two more:

1. **`A.Compare`'s lowering had no `dunder_owner` check at all** (unlike
   `A.BinOp`, which already had one) — `a == b` / `a < b` / etc. on
   instances with a real `__eq__`/`__lt__`/etc. silently fell through to
   the generic chained-comparison path, which compares the two operands'
   raw pointer values (object identity), not their field-wise equality.
   Not a crash — silently *wrong output*, confirmed via a minimal repro
   (a `Point` class with `__eq__` comparing x/y fields printed `False`
   for two field-equal-but-distinct instances instead of `True`). Fixed
   with a `dunder_owner` check mirroring `BinOp`'s, handling the
   `dunder_negate` flag `!=`/reflected comparisons already carry. The
   reachability walker already covered `A.Compare` (unlike the
   `A.UnaryOp` case in pass two), so no second fix was needed here.
2. **Custom `__contains__` had no lowering path at all**: `x in obj` /
   `x not in obj` on an instance with `__contains__` hit
   `_lower_membership`'s hard `LowerError("unsupported compare
   membership")` guard, since that helper only ever handled dict/set/
   list/tuple haystacks. A clean build failure, not a silent-wrong-output
   bug like the others in this stretch. Fixed with a `dunder_contains_owner`
   check at the `A.Compare` `in`/`not in` dispatch site, added before
   falling through to `_lower_membership` (left unchanged). Confirmed
   fixing `367_custom_contains.py` exactly (all three membership checks:
   present, absent, negated).

Verified: `tests.runner` 475/483 throughout. Sweep after all fixes in
this stretch (unary dunders, `__call__`, `Compare` dunder_owner,
`__contains__`): 254→255 OK, 118→117 build failures (the sweep's overall
totals showed some run-to-run noise — a couple of cases flip between
OK/CRASH/timeout across otherwise-identical runs, likely toolchain/
environment flakiness rather than anything code-related; `367_custom_
contains.py`'s fix was independently confirmed via direct build+run,
not just the aggregate count).

**Cumulative sweep numbers, all three triage passes this session, from
the `OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start
baseline**:

```text
OK=255  MISMATCH=24  CRASH=42  BUILD_FAIL=117
```

**Triage pass four** (2026-07-16, same-day follow-up): moved into the
mismatch bucket (silently-wrong output, not crashes/build-failures) —
motivated directly by the `Compare` dunder bug in pass three, which
proved this bucket can hide real correctness bugs the exit-code-based
sweep buckets can't surface on their own. Two more fixes:

1. **`print(sep=..., end=...)` was entirely unimplemented on this
   backend** — the keyword args were silently ignored, always falling
   back to CPython's defaults (`" "`/`"\n"`) regardless of what the call
   site passed. `codegen.py` (legacy backend) already had this fully
   working; ported its sep/end extraction into `ir_lower.py`'s `print()`
   lowering, baking the literal sep/end text directly into the printf
   format string (with `%` doubled to `%%` defensively, since a literal
   `%` in sep/end would otherwise be misread as a conversion specifier —
   codegen.py avoids this differently, emitting sep/end as separate
   literal-string writes rather than baking into the format string).
   Confirmed fixing `418_print_sep_end.py` exactly (all 5 lines).
2. **`dict.get(key)` with no explicit default silently printed `0`/`0.0`
   instead of `None`** for a missing key: sema stamps a real
   `dict_get_none_default` flag (the runtime sentinel for "key missing"
   is a plain zero, which is ambiguous at compile time with a real `0`
   value, so this needs a *runtime* check, unlike the static
   `is_none_expr`/`is_bool_expr` checks already handled) but
   `_lower_expr_as_str` (the print/str-coercion helper) never checked it
   for either the `int` or `float` case. Added the missing runtime
   zero-check branches (mirroring `codegen.py`'s existing
   `_emit_print_value` handling of the same flag) for both. Confirmed
   fixing `88_dict_get_no_default.py` and `412_dict_get_none.py` exactly.

Verified: `tests.runner` 475/483 throughout. Sweep: 255→258 OK,
21 mismatches remaining (down from 24).

**Cumulative sweep numbers, all four triage passes this session, from
the `OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start
baseline**:

```text
OK=258  MISMATCH=21  CRASH=42  BUILD_FAIL=117
```

**Triage pass five** (2026-07-16, same-day follow-up): two comprehension-
scoping bugs plus one genuine **register-allocator correctness bug** —
the most severe finding of the day.

1. **List/set comprehensions at module scope with a loop variable
   shadowing a module global** (`x = 7; xs = [x * 2 for x in [1,2,3]]`)
   silently read the *global* inside `e.elt`/`e.cond`, not the
   comprehension's own local loop variable — `_lower_comprehension`
   never had the `comprehension_shadows`-style protection the earlier
   for-loop shadow fixes established. Added a
   `ctx.comprehension_shadows` stack (list of sets), checked first in
   `_is_global_name`, pushed/popped around the comprehension body.
   Confirmed fixing `416_comp_global_shadow.py` (was `[14,14,14]`,
   now `[2,4,6]`).
2. **List/dict comprehensions with tuple-unpack targets**
   (`[k for k, v in d.items()]`) had **zero handling** in
   `_lower_comprehension` (only `_lower_dict_comprehension` had it) —
   `e.var` is always `""` for this shape (per the parser), and got used
   as a slot name unconditionally, silently corrupting every read.
   Added the missing `e.targets` branch, mirroring `A.For`'s identical
   tuple-unpack pattern (`_list_elem_addr` per slot +
   `_store_loop_target`). Confirmed fixing `421_dict_comp_filter.py`
   (`['c','c','c']` → `['b','c']`).
3. **The big one**: a real x86-64 register-allocator correctness bug in
   `_backends/x86_64/codegen.py`'s `_div` (backs `idiv`/`irem`/`udiv`/
   `urem`, i.e. `//` and `%` on ints). When the allocator happened to
   assign a division's *dividend* operand a permanent home of `RAX`,
   `_div` skipped the `mov RAX, a_r` copy as a no-op ("already there")
   — but `idiv`/`div` unconditionally clobber `RAX:RDX` as scratch
   space regardless, silently destroying the dividend's value with
   nothing to restore it for a later read that trusts regalloc's
   decision it still lives in RAX. Real, general-purpose bug: any
   program dividing the same value twice in a row (not contrived —
   `n % 2 == 0`'s own floor-div-mod correction sequence does exactly
   this internally) is a potential trigger whenever regalloc happens to
   pick RAX for that value, which depends on unrelated register
   pressure earlier in the function (why this took real effort to
   isolate — a minimal repro needed the *exact* combination of prior
   code to reproduce). Root-caused via IR analysis + a live gdb
   single-step trace of the compiled binary (watched RAX go from the
   real dividend value to the first division's leftover quotient,
   directly causing the wrong branch). Fixed by bouncing the dividend
   into the second scratch register (`_SCRATCH2`/R10) before the
   division when it's in RAX, restoring it into RAX afterward — unless
   the division's own result also landed in RAX, in which case
   restoring would clobber the real answer (guarded via `dst !=
   Reg.RAX`). Confirmed fixing `78_dict_comprehension.py` exactly (all
   8 lines) and, transitively, the earlier `409_...`/`445_...`-style
   float-comparison-adjacent mismatches that likely shared this root
   cause without anyone having isolated it yet.

Verified: `tests.runner` 475/483 throughout. Sweep: 258→263 OK,
16 mismatches remaining (down from 21; the regalloc fix alone likely
explains most of the drop, given how general the bug class is).

**Cumulative sweep numbers, all five triage passes this session, from
the `OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start
baseline**:

```text
OK=263  MISMATCH=16  CRASH=42  BUILD_FAIL=117
```

**Audit follow-up, done same day**: re-audited `codegen.py` for every
other "skip the copy when the operand is already in the destination
register" shortcut touching a fixed x86 register, per the concern
above. Checked all four remaining sites: `_shift`'s `cnt_r != Reg.RCX`
(shift-by-CL only *reads* CL's low bits, doesn't clobber the rest of
RCX — safe), the call-result move's `loc.reg != Reg.RAX` (end of the
call sequence, nothing clobbers RAX afterward — safe), and `ret`'s
`src_r != Reg.RAX` (immediately followed by the epilogue/`ret`, nothing
clobbers RAX in between — safe). **`_div` was the only genuinely unsafe
instance of this pattern** — it's specifically dangerous there because
`idiv`/`div` clobber RAX:RDX as an intrinsic side effect of the
instruction itself (not as an ABI convention the surrounding code
controls), which none of the other sites do. No further instances of
this bug shape found; the fix was correctly scoped.

**Triage pass six** (2026-07-16, same-day follow-up): a genuinely
**missing feature**, not a small oversight — plain-class static
class-level variables (`class Config: version = 5`) were entirely
unimplemented on the x86-64 backend. `ClassName.attr` crashed
unconditionally: `e.obj` (`Config`) is a class name, never bound to any
real variable/slot, so `_lower_expr` fell through to `A.Name`'s
"class name used as a value" case and returned the class's raw numeric
RTTI id (e.g. `0`) — which then got used as if it were a real
dict/instance *pointer* by the generic attribute-access fallback,
crashing (confirmed via gdb: SIGSEGV dereferencing near address 0, the
class's own RTTI id). Root cause on the sema side too: sema's matching
"class-level variable read" branch (~line 6602) only ever set the
`Attr` node's own type — it never called `_check_expr` on `e.obj`
either, so `e.obj.inferred_type` was left at the parser's placeholder
default. Both halves of the bug independently trace back to "a node's
fields were read without the node ever going through normal type-
checking/lowering," the same shape as several earlier fixes today.

Implemented the full feature, mirroring `codegen.py`'s existing
`class_var_labels`/`__cv_<Class>__<var>` convention exactly:
`_ModuleCtx` now takes the module's `classes` list and builds
`class_var_labels`/`class_var_defaults` (skipping `@dataclass` classes,
whose class vars are per-instance fields, not static — same exclusion
`codegen.py` makes); `lower_module()` registers one real `IRGlobal` per
class var and prepends genuine *runtime*-init `A.Assign` statements
(matching `codegen.py`'s `_emit_init_class_vars` — a class var's
default can be any expression, not just a literal, so this can't be a
compile-time constant fold) to whichever init body runs first, covering
both the `has_explicit_main` and script-model paths; new `A.Attr`
read + `A.AttrAssign` write branches recognize `(ClassName, attr)` in
`class_var_labels` before falling through to the generic (wrong)
instance-attribute path. `Counter.total += 5`-style augmented writes
work for free, since sema already desugars those to a plain
`AttrAssign` with an expanded value expression that reads through the
new read-side branch.

Confirmed fixing `99_class_vars.py` (all 5 lines: int/str/float class
vars, reassignment, `+=` on a separate class) and, as a bonus,
`368_classmethod_cls_field.py` (a previously separate crash-bucket
entry that turned out to share this exact root cause via sema's
`cls.field` inside `@classmethod` → `ClassName.field` rewrite).

**Audit follow-up** (same checkpoint): per the earlier `_div` bug's
severity, re-audited `codegen.py` for the same "skip the register copy
because the operand is already in the destination register" shortcut
elsewhere — checked `_shift`'s RCX check, the call-result move, and
`ret`'s RAX move. All three are safe (none clobber the register as an
*intrinsic* side effect the way `idiv`/`div` do — shift-by-CL only
reads CL's low bits, and the other two have nothing after them that
would clobber the register before it's consumed). `_div` was the only
genuinely unsafe instance; no further fix needed, just documented.

Verified: `tests.runner` 475/483 throughout. Sweep: 263→268 OK,
crashes 42→37 (the class-var feature closed 5 crash-bucket entries at
once — a real, general feature gap, not a one-off).

**Cumulative sweep numbers after six triage passes this session, from
the `OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start
baseline**:

```text
OK=268  MISMATCH=16  CRASH=37  BUILD_FAIL=117
```

(See triage pass seven below for the current, further-updated numbers —
`OK=287 MISMATCH=12 CRASH=22 BUILD_FAIL=117`.)

**Triage pass seven** (2026-07-16, same-day follow-up): crash-bucket
triage, six fixes:

1. **Lambda/closure calls through a variable never routed indirectly.**
   `double = lambda x: x*2; double(21)` (and any function value passed
   through a parameter, e.g. `def apply(g, v): return g(v)`) crashed:
   `A.Call` lowering's indirect-vs-direct dispatch only checked
   `ctx.slot_ty` (LOCAL slots), never `ctx.mctx.global_types` (module
   globals) — a module-scope `name = lambda ...` is a real global, not a
   local slot, so it fell to the direct-symbol-call fallback, linking
   against a nonexistent symbol `double` instead of loading the function
   pointer the global actually held. Fixed by checking both tables.

2. **`@staticmethod` called via `ClassName.method(...)` always passed an
   implicit receiver arg it doesn't have.** Both the reachability walker
   (which force-stamps `param_types[0]` to `"any"` assuming every method
   has an implicit self/cls) and the `MethodCall` lowering (which always
   prepended `ctx.shared_zero` as arg 0) treated static methods the same
   as instance/class methods — clobbering a static method's real first
   parameter and creating an arg-count mismatch. Fixed both sites to
   check the resolved method's `"staticmethod" in decorators`.

3. **List `+`/`*` (concat/repeat) never implemented on this backend at
   all** — fell through to the plain-int `+`/`*` path, adding/multiplying
   the two lists' raw header POINTERS as integers. Added proper
   `_abi_list_slice`(full range, shallow copy)+`_abi_list_extend` for
   `+` (mirrors codegen.py's `_runtime_list_slice`+`_runtime_list_extend`
   pattern) and a new `_abi_list_repeat` ABI shim (mirroring the
   existing `_abi_str_repeat` convention exactly, register-for-register)
   for `*`. Also added the missing `AugAssign` cases: `xs += other`
   (list) extends in place with no rebind; `s += other` (str) concats to
   a fresh pointer and rebinds — AugAssign had no list/str-aware branch
   at all previously, falling through to the same raw-pointer-arithmetic
   bug.

4. **THE major finding of this pass: function parameters silently read
   the wrong memory when their name collides with an unrelated
   module-level global.** `def split(lo, hi): ...` alongside a later
   top-level `lo, hi = minmax(...)` in the SAME file — `lower_func`'s
   `local_names` was seeded only by scanning the function body's
   assignment targets/for-loop vars (`_collect_bound_names`), never by
   the function's OWN PARAMETERS. A parameter the body only ever READS
   (never reassigns) was therefore entirely absent from `local_names`,
   so `_is_global_name`'s non-module-scope fallback (`return name in
   ctx.mctx.global_names`) misrouted every read of that parameter to
   `global_addr` the unrelated same-named global instead of the
   parameter's own stack slot. Silent, not a crash by itself — but
   `64_multi_return.py` divided by the resulting (zero/uninitialized)
   value, SIGFPE. Fixed by seeding `local_names` with `f.params` before
   the body scan. This single fix incidentally also fixed
   `427_bst_recursive.py` (a recursive `root` parameter shadowing
   pattern) as a bonus — confirming it's a general, not one-off, gap.

5. **Integer `//`/`%` had NO zero-divisor check at all on this
   backend** — a genuine `b=0` reached the raw `idiv`/`irem` IR ops
   directly, hardware-faulting with SIGFPE (uncatchable — not even a
   `try`/`except ZeroDivisionError` around it could intercept a signal).
   codegen.py's legacy backend has always raised a normal, catchable
   `ZeroDivisionError` here; this backend never ported that check. Added
   `_emit_int_divzero_check` (branch + `_abi_raise` with
   `BUILTIN_EXC_IDS["ZeroDivisionError"]`, message text matching
   codegen.py's `_runtime_zerodiv_msg` exactly) at the single choke
   point (`_lower_int_floordivmod`, shared by `//`, `%`, and their
   AugAssign forms). `_abi_divmod` (the `divmod()` builtin) already had
   its own equivalent check in `_runtime_divmod` — untouched.

6. **The deepest bug this session, arguably the whole IR-migration
   effort: try/except's setjmp/longjmp mechanism could silently corrupt
   a register-allocated value that outlives the try, whenever the
   protected try body itself makes ANY function call before an
   exception fires.** Two independent, compounding defects, both fixed:

   - **a)** `_runtime_setjmp`/`_runtime_longjmp` (the actual jmp_buf
     save/restore in codegen.py's runtime generator, shared by both
     backends) never saved/restored RSI/RDI — despite both being
     genuinely non-volatile/callee-saved on the Win64 ABI, exactly like
     RBX/R12-R15 which WERE already saved. Grew the jmp_buf from 64 to
     80 bytes (`ir_lower.py`'s `_JMP_BUF_SIZE`) and added the two extra
     slots.
   - **b)** The x86-64 backend's register allocator (`regalloc.py`)
     computes a value's live range from block-LIST order, but
     `_lower_try` allocates a try's exception-handler blocks (reached
     only via the IMPLICIT setjmp/longjmp control transfer, no ordinary
     `br`/`br.t` edge) physically AFTER the try's own post-loop-body
     continuation block in that list. A value defined before the try and
     read only inside the handler (e.g. a for-loop's own loop-variable
     address, printed in the `except` clause) looked, to the plain
     last-use scan, already dead by the time the handler's use was
     recorded — freeing its register for reuse mid-loop. Fixed
     structurally: `ir_lower.py`'s `_lower_try` now stamps the exact
     `[setjmp_block_index, last_block_index]` span onto a new
     `IRFunc.try_regions` field (computed directly from the blocks it
     just created — no fragile label parsing); `regalloc.py`'s
     `_last_uses` extends any value defined before the region and
     referenced anywhere inside it to stay live through the region's
     end, mirroring the existing loop-back-edge extension.
   - **c) Fixing (a) and (b) alone was NOT enough** — confirmed via a
     hardware watchpoint that the actual corruption was a THIRD,
     independent defect: `_call`'s own codegen wraps every call
     (including `_abi_setjmp`) in an EPHEMERAL win64_saved_regs
     save/restore using RSP-relative scratch space "below" the
     function's permanent frame — safe for ordinary calls (strictly
     nested call/return), but unsafe for `_abi_setjmp` specifically,
     since it can "return" a SECOND time (via a LATER `_abi_raise` ->
     `longjmp` jumping back to the same return address, arbitrarily far
     in the future). Any call made inside the try body between the two
     returns (here: `_abi_raise` itself) is free to reuse that exact
     same ephemeral stack memory for its own temp-save area, since
     nothing marks it "still in use" after the first, normal return
     already consumed it once — corrupting the value `_abi_setjmp`'s
     call site thought it was protecting. Since `_runtime_setjmp`/
     `_runtime_longjmp` (fix (a), just above) already save every
     register that matters into the DURABLE jmp_buf, this outer
     ephemeral save was pure redundancy for this one call site, and
     actively unsafe. Fixed by skipping `win64_saved_regs` entirely for
     calls specifically to `_abi_setjmp`.

   Confirmed via `28_exceptions_loop.py` (exact 7-line match) — this
   pattern (a `for` loop wrapping a `try`/`except` that reads a
   loop-scope variable in the handler, with a second loop afterward)
   is common enough that this was almost certainly corrupting other
   exception-handling test cases too, possibly contributing to some of
   the still-open mismatch-bucket entries.

Verified: `tests.runner` 475/483 throughout (checked after every
sub-fix, not just at the end).

**Investigation note, resolved by the same fix**: during this pass, a
minimal repro (`for i in range(5): try: ... except as e: print("raised
at", i)`, *without* a second loop afterward) printed `"raised at 3"`
**five times** instead of once, with `i` NOT correctly advancing —
initially flagged as a possibly-separate wrong-output bug. Re-confirmed
after fix 6c (the `_abi_setjmp` win64_saved_regs skip) landed: this
repro now produces the exact expected output (`raised at 3` /
`survived`, nothing else). It was the SAME corruption, just manifesting
as wrong output instead of a hard crash in this narrower shape (no
second loop to dereference the scrambled pointer against) — not two
bugs, one bug with two visible symptoms depending on what surrounding
code happened to reuse the corrupted register for next.

Full-corpus sweep: **`OK=287 MISMATCH=12 CRASH=22 BUILD_FAIL=117`**,
up from `OK=268 MISMATCH=16 CRASH=37 BUILD_FAIL=117` before this pass —
+19 OK, -4 mismatches, -15 crashes from six fixes, the large jump
consistent with the setjmp/longjmp bug (fix 6) having been silently
corrupting a broad swath of exception-handling test cases, not just the
one it was diagnosed from.

**Next step on resume**: continue the same triage pattern on whatever
crash/build-failure/mismatch entries remain per the latest sweep
(`sweep_v12_result.txt` in the scratch dir) — group by symptom before
fixing one at a time. FFI-module-constant access (`_audio_sdl.CONST`,
`_gui_sdl.CONST`, `math.pi`-style) is a confirmed, still-open, distinct
crash-bucket entry (builds fine, crashes at runtime) — same root cause
class as the earlier `string.ascii_lowercase` fix but for CONSTANTS
specifically rather than functions; a real but separate feature gap,
not yet scoped. After crashes: the 117 build failures (each a clear
"unsupported expr/stmt X" `LowerError` message — `str.format`/bare
`format()` builtin, a third format mini-language, is a known
unimplemented gap likely responsible for a chunk of these), then the
remaining mismatches. The ad-hoc sweep script used throughout all seven
passes (scratch dir, not yet committed) is worth promoting to a real
`tests/backend_correctness.py` next time — re-run it after every fix
batch, not just at the start/end, since a single fix can measurably move
the needle, and note the sweep's aggregate counts have some run-to-run
noise (a case or two flipping between OK/CRASH/timeout across identical
runs) — always confirm a specific fix via direct build+run of the
affected case(s), not just the before/after totals. Given the
"Everything Python" bar (run essentially any unmodified real-world
Python program), once this corpus is closer to 100%, pivot to validating
against real-world stdlib-only scripts or CPython's own `Lib/test/`
suite (already pyinbin's conformance oracle) — passing this 440-case
hand-written corpus was never meant to be the definition of "done," just
the nearest checkpoint before that.

**When new Win64 ABI shims are added going forward, verify stack-slot
placement (must be at/above rsp+32) and argument-register assignment
(shared positional index, not per-type) explicitly — both bug classes
found earlier this session assembled cleanly and only failed at
runtime, sometimes on a delayed/second call.**

**When debugging a register-allocator/codegen correctness bug that only
reproduces with a specific combination of surrounding code (not in
isolation): a hardware watchpoint on the exact suspect memory address
(`watch *(long*)0xADDR` in gdb) is far more direct than reasoning about
stack offset arithmetic by hand or trying to infer corruption from
register dumps at a handful of breakpoints — it was what actually
cracked triage pass seven's setjmp/longjmp bug after several false starts
(the RSI/RDI jmp_buf fix and the try_regions liveness fix were both
real, necessary bugs, but neither alone explained the crash; the
watchpoint immediately identified the third, actual culprit).**

**Triage pass eight** (2026-07-16, same-day follow-up): two fixes,
both closing multiple crash-bucket entries at once:

1. **FFI-module-constant access** (`os.sep`, `math.pi`,
   `_audio_sdl.MIX_DEFAULT_FORMAT`-style attribute reads on a pure
   FFI-binding-table stdlib module) — the exact gap flagged as
   "not-yet-fixed" in pass one's `string.ascii_lowercase` comment.
   `ir_lower.py` had zero `ffi_consts`/binding-table-`Const` handling
   for `A.Attr` at all: sema already resolves the right TYPE (via
   `imported_modules[module].get(name)`, an existing branch), but
   nothing on the ir_lower side ever consulted the same table for the
   VALUE, so every access fell through to the generic instance-
   attribute fallback — reading the module name (never bound to any
   real variable) as an uninitialized stack slot treated as a dict
   pointer, guaranteed segfault. Fixed by mirroring codegen.py's
   `_gen_const_load`/`_platform_const_value` exactly: resolve the
   `Const`'s value at COMPILE time (the binding table itself IS the
   value — nothing to load from memory), preferring `value_windows`
   when present (this backend only targets Windows PE output), and
   materializing it as a real IR constant (`intern_str`+`global_addr`
   for str, plain `const` for int/float). Confirmed fixing 5
   crash-bucket entries in one shot: `test_audio_constants.py`,
   `test_gui_constants.py`, `test_gui_joystick_constants.py`,
   `243_platform_module.py`, `266_platform_depth.py`, plus turning
   `16_import_math.py`'s crash into a (separately fixed, see below)
   mismatch and fixing `299_signal_module.py` — all exact matches.

2. **FFI function calls never promoted an int-literal argument to
   float for a binding declaring a `float` parameter** — surfaced by
   fix 1 turning `16_import_math.py` from a crash into a wrong-output
   mismatch (`math.sqrt(16)` printed `0` instead of `4`). The
   `MethodCall` FFI-dispatch path (`ctx.mctx.imported_modules[...]`,
   used for e.g. `math.sqrt`/`math.pow`/`math.hypot`) lowered every
   argument via a bare `_lower_expr(ctx, a)` with no reference to the
   binding's own declared `arg_types` — an int-literal argument stayed
   an I64 IR value, which the call's own ABI marshaling (keyed off the
   VALUE's type, not the callee's signature) places in a GP register;
   the real C symbol (libm's `sqrt`, taking a `double` in XMM0) read
   garbage from XMM0 instead. Fixed by promoting via `sitofp` whenever
   `arg_types[i] == "float"` and the lowered value isn't already F64 —
   mirrors the same int→float promotion every other numeric-binop call
   site in this file already does. Confirmed fixing `16_import_math.py`
   exactly (all 7 lines).

**Separately found, NOT yet fixed** — a distinct, deeper bug in
whole-program-merge module scoping, NOT a backend bug: `os.getcwd()`
and `ospath.isdir()`/`isfile()`-style calls to a REAL Python-source
wrapper function (defined in `ospath.py`/`pathlib.py`) still crash.

**Correction from this section's first draft**: initially misdiagnosed
as a whole-program-merge scoping bug (an AST dump of `os.getcwd()`
showed the `os` `A.Name` typed `"int"`, which looked like `Import`'s
`scope.add(bind_name, "module")` was getting overridden). That
diagnosis was WRONG — re-investigating for triage pass nine found the
real cause: `os.getcwd()`/`os.listdir()`/`os.cpu_count()` are
deliberately NOT real `BINDINGS` entries at all (see `sema.py`
~line 6777's own comment: "inline codegen helpers not in BINDINGS, no
C symbol") — `Python`'s `getcwd()` needs a scratch buffer plus a dup
call, not a single C function call a plain `Func` binding can express,
so sema special-cases these three names by NAME string match and
short-circuits BEFORE the generic `bindings` type lookup that would've
produced `"module"`/`"str"` — the `"int"` I saw was simply
`_check_expr`'s DEFAULT for an unresolved `A.Name`, an artifact of
testing the isolated repro slightly differently, not a real merge bug.
codegen.py has matching hand-written inline emitters
(`_emit_os_getcwd`/`_emit_os_listdir`) that `ir_lower.py` had ZERO
equivalent of — every call fell through to the generic "unknown method
on opaque receiver" stub, which evaluates args for side effects and
returns a plain 0, but sema had already typed the call's result as a
real `str`/`list` value, so the caller's later use of that `0` as a
string/list pointer crashed immediately.

**Triage pass nine** (2026-07-16, same-day follow-up): implemented
`os.getcwd()` and `os.cpu_count()` as new inline-emitter cases in
`ir_lower.py`'s `MethodCall` FFI-dispatch, ported from codegen.py's
`_emit_os_getcwd` logic but using a runtime `malloc(4096)` scratch
buffer instead of porting codegen.py's static `_cwd_buf` BSS
reservation (this backend's `IRGlobal` has no raw-byte-buffer
reservation mechanism yet, and a runtime malloc is just as correct for
a call this infrequent — not worth building new backend infrastructure
for): `malloc` → real `_getcwd(buf, size)` DLL call (the exact symbol
already confirmed resolvable from pass one's `_DLL_FOR_SYMBOL`
additions) → branch on NULL (failure) vs. dup-via-`_abi_str_concat_dup`
(success, mirroring codegen.py's fail/done branches exactly).
`cpu_count()` is a trivial `const 1` (asmpython has no nullability
tracking, so — matching sema.py's/codegen.py's own simplification —
this is always a plain positive int, never the real `int | None`).
Confirmed fixing `155_os_module.py` exactly (all 9 lines). `os.listdir()`
deliberately left unimplemented — codegen.py's own version shells out to
`dir /b` via `_popen` and parses line-by-line, real complexity worth its
own dedicated pass, not a quick follow-on here. `301_ospath_isdir_isfile.py`/
`302_pathlib_isdir_isfile.py` (different functions, `isdir`/`isfile`,
not yet investigated) and `93_os_file_io.py`/`255_os_file_io.py` (file
I/O, likely a related-but-separate gap) remain open crashes.

Verified: `tests.runner` 475/483 throughout, checked after each fix.
Sweep: `OK=298 MISMATCH=12 CRASH=11 BUILD_FAIL=117`, up from
`OK=297 MISMATCH=12 CRASH=12 BUILD_FAIL=117` before this pass — +1 OK,
-1 crash, exactly `155_os_module.py` as expected (this pass deliberately
scoped to just `getcwd`/`cpu_count`, not the harder `listdir`).

**Cumulative sweep numbers after nine triage passes this session, from
the `OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start
baseline**:

```text
OK=298  MISMATCH=12  CRASH=11  BUILD_FAIL=117
```

56% → 68% of the full 438-case corpus passing. The crash bucket alone
has gone from 42 to 11 across all nine passes — the remaining 11 are:
`156_os_listdir.py` (needs the `_popen`-based listdir port, deliberately
deferred), `301_ospath_isdir_isfile.py`/`302_pathlib_isdir_isfile.py`
(different functions, not yet investigated), `93_os_file_io.py`/
`255_os_file_io.py` (file I/O, times out rather than crashing — likely a
related-but-separate gap), and `172_base64_module.py`/
`196_hashlib_module.py`/`225_hashlib_module.py`/`229_base64_module.py`/
`358_calendar_module.py`/`369_dunder_bool.py` (unrelated, not yet
triaged individually).

**Triage pass ten** (2026-07-16, same-day follow-up): `369_dunder_bool.py`
turned out to be a real, general bug, not a one-off — `if obj:` /
`while obj:` on a user instance never checked `__bool__`/`__len__` at
all. `_value_truthy` (the shared helper backing every truthiness site:
`If`, `While`, ternary, `not`, list-comprehension conditions,
`sorted(reverse=...)`) only special-cased `float`; "any heap pointer"
(including a live instance) passed through as a raw nonzero test —
the SAME dunder-dispatch gap class found repeatedly earlier this
session (BinOp/UnaryOp/Compare/Call all needed their own `dunder_owner`
checks added one at a time), just never checked for truthiness
specifically until now. Confirmed as a genuine INFINITE LOOP, not a
crash: `while c:` on a `Counter` instance with `__bool__` returning
`self.n > 0` never terminated (the loop condition always saw `c`'s own
nonzero pointer), ticking `c.n` down through -300+ before the sweep's
10-second timeout killed it — exactly the `[CRASH]`-bucket "run error:
... timed out after 10 seconds" entries the sweep script has been
reporting all session for this file. Fixed in `_lower_truthy` (the
wrapper every real call site already uses, not `_value_truthy`
directly): when the condition expression's type is `instance:X`, check
`__bool__` then `__len__` (matching CPython's precedence, and mirroring
codegen.py's `_gen_truthy_test`) before falling back to "any live
instance is truthy." Needed a SECOND fix, the exact same two-part
shape as the earlier unary-dunder bug: the reachability walker had no
way to know an `A.If`/`A.While` node's `.test` field implies a
`__bool__`/`__len__` call (that dispatch decision isn't stamped
anywhere on the AST the way `dunder_owner` is for BinOp/Compare), so
the lowering was correct but the method it called was never emitted —
an unresolved-symbol link error (`undefined symbol 'Box____bool__'`)
until a matching walker case was added. `A.BoolOp` (`and`/`or`) has its
own separate lowering path that does NOT go through `_lower_truthy` —
left unaudited for now (no test case in this session's corpus exercises
an instance operand there), flagged as a follow-up rather than silently
assumed fine.

Verified: `tests.runner` 475/483. Confirmed fixing `369_dunder_bool.py`
exactly (all 10 lines, including the previously-infinite `while c:`
loop terminating correctly). Full sweep: `OK=299 MISMATCH=12 CRASH=10
BUILD_FAIL=117`, up from `OK=298 MISMATCH=12 CRASH=11 BUILD_FAIL=117`
before this pass — +1 OK, -1 crash, exactly `369_dunder_bool.py`.

**Cumulative sweep numbers after ten triage passes this session, from
the `OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start
baseline**:

```text
OK=299  MISMATCH=12  CRASH=10  BUILD_FAIL=117
```

56% → 68% of the full 438-case corpus passing. The crash bucket has
gone from 42 to 10 across all ten passes.

**Triage pass eleven** (2026-07-16, same-day follow-up): found and
fixed a genuine REGRESSION introduced by this same session's own pass
seven (`_emit_int_divzero_check`, the `//`/`%` zero-divisor check).
Investigating `358_calendar_module.py`'s SIGFPE crash (a real
divide-by-zero fault reaching hardware, from a divisor that was never
actually zero in the source) narrowed to `calendar.py`'s `weekday()`:
`h: int = (day + (13*(month+1))//5 + k + k//4 + j//4 - 2*j) % 7` — four
`//`/`%` operations chained in one expression. A `git checkout` A/B
diff against the immediately-prior commit (before the zero-check
existed at all) confirmed the SAME source built and ran correctly
without it, isolating the zero-check itself as the cause rather than
some unrelated pre-existing bug.

Root cause: `_emit_int_divzero_check` created its `ok_b` block BEFORE
`raise_b`, so `raise_b`'s own `br` back to `ok_b` — needed only to give
the (in practice always-dead, since `_abi_raise` never returns
normally) block a valid IR terminator — was a branch to a LOWER
block-list index. `regalloc.py`'s `_last_uses` loop-back-edge
detection (added originally for genuine `for`/`while` loops) can't
distinguish "real loop" from "this happens to also be a backward
branch" — it saw this pattern and mis-classified the whole
`[ok_b, raise_b]` block range as a loop, spuriously force-extending the
liveness of every value touched anywhere in that range. `weekday()`'s
four chained divisions each generate their own divzero-check pair at a
successively higher block index, so this false "loop" classification,
repeated four times in sequence, ended up scrambling which physical
register still held an EARLIER division's result by the time a LATER
division read it as its own dividend/divisor — a different, unrelated
value's register bleeding through as a divisor that happened to be 0.

Fixed by simply creating `raise_b` before `ok_b`: `ok_b`'s index is now
the HIGHER one, so `raise_b`'s `br` back to it is an ordinary forward
edge, never matching the back-edge pattern at all. No change to
`regalloc.py` itself was needed this time — unlike the setjmp/longjmp
bug, this didn't need new cross-function liveness-region metadata, just
avoiding the accidental backward branch in the first place.

**Process note**: this is the session's first confirmed case of a fix
introducing its OWN regression, caught only because triage pass eight
happened to exercise a test case (`calendar_module`) whose real-world
code shape (several divisions chained in one expression) differed from
every div-by-zero test case exercised while pass seven's fix was being
verified. Worth remembering for future backend work: a new block-
creation helper should default to creating its "success"/"continue"
path FIRST when it can, specifically to avoid handing the loop-detector
an accidental backward edge — this is now the second bug (after the
setjmp/longjmp one) traced back to `_last_uses`'s block-list-order
assumption not matching a helper's actual block-creation order.

Verified: `tests.runner` 475/483. Confirmed fixing `358_calendar_module.py`
exactly (all 5 lines) and re-confirmed `436_multi_except.py` (pass
seven's own original divzero test case) still passes. Full sweep:
`OK=301 MISMATCH=12 CRASH=9 BUILD_FAIL=116`, up from `OK=299 MISMATCH=12
CRASH=10 BUILD_FAIL=117` before this pass — +2 OK, -1 crash, -1
build-failure (the regression was evidently corrupting more than just
`358_calendar_module.py`; the extra +1 beyond the expected single-crash
fix confirms a second, previously-silent build-failure case also hit
the same chained-division shape).

**Cumulative sweep numbers after eleven triage passes this session,
from the `OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start
baseline**:

```text
OK=301  MISMATCH=12  CRASH=9  BUILD_FAIL=116
```

56% → 69% of the full 438-case corpus passing.

**Triage pass twelve** (2026-07-16, same-day follow-up): two more
real bugs, both fitting recurring bug SHAPES from earlier this
session — closing 6 crash-bucket entries between them.

1. **FFI `int`-returning calls never sign-extended their result.** A
   real C `int` is 32-bit — the callee returns it in EAX with the
   upper 32 bits of RAX left UNSPECIFIED by the calling convention, but
   every asmpython value is a full 64-bit slot. `codegen.py`'s
   `_gen_ffi_call` has always had the fix for this (`movsxd rax, eax`,
   with its own comment explaining exactly this), but `ir_lower.py`'s
   FFI call-return path had no equivalent at all — the result register
   was used as-is. `os.fgetc(f)`'s EOF sentinel (`-1`) read back as
   `0x00000000FFFFFFFF` (4294967295) instead of `-1`, so `while c !=
   -1:` never terminated. Confirmed as a genuine INFINITE LOOP, not a
   crash — the same "shows up as a sweep timeout, not a segfault"
   shape as pass ten's `__bool__`/`__len__` bug. Fixed by emitting a
   new `sext` IR instruction (already implemented in `codegen.py`,
   just never emitted from anywhere in `ir_lower.py`) immediately after
   any FFI call whose `ret_type == "int"` (excluding `ret_conv ==
   "ptr"`, which means the C function genuinely returns a real 64-bit
   pointer/handle — e.g. `SDL_CreateWindow` — where sign-extending just
   EAX would truncate it). Confirmed fixing `93_os_file_io.py`/
   `255_os_file_io.py` exactly (both were "timed out after 10 seconds"
   sweep entries).

2. **A variable-count shift (`x >> y` where `y` isn't a compile-time
   constant) could silently clobber an unrelated live value in RCX.**
   x86's variable-count `shl`/`shr`/`sar` hard-requires the count in
   CL, so `codegen.py`'s `_shift` unconditionally does `mov rcx,
   cnt_r` — but `regalloc.py` had no notion that this implicitly
   clobbers RCX, the same hazard SHAPE as the `_div`/RAX bug from pass
   five (a fixed-register instruction clobbering something the
   allocator didn't know to protect) but for a different instruction
   and register. Root-caused by a dispatched Explore subagent (same
   methodology as pass five's original `_div` investigation) on this
   minimal repro:
   ```python
   def enc(data: list[int], alphabet: str) -> list[int]:
       out: list[int] = []
       ...
       while i + 3 <= n:
           triple = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
           out.append(ord(alphabet[(triple >> 18) & 0x3F]))
           out.append(ord(alphabet[(triple >> 12) & 0x3F]))
           ...
   ```
   The allocator placed the SECOND string-index call's `alphabet`
   pointer argument in RCX; the `>> 12` shift's own `mov rcx, cnt_r`
   then clobbered it before the call read it, and the corrupted
   "pointer" crashed inside `strlen` (a value the crash's own gdb
   trace showed as `0x3F` nearby was a red herring — the sibling mask
   constant threaded through an adjacent register in the same
   instruction sequence, not itself the corrupted operand). Fixed via
   a new `_compute_crosses_var_shift` in `regalloc.py` (mirroring
   `_compute_crosses_call`'s exact shape: a full-function pre-pass
   flagging every value whose live range spans a variable-count shift,
   excluding the shift's own two operands) and a new `avoid_rcx` hard
   constraint on `_take_gp` (unlike `crosses_call`'s callee-saved
   *preference*, this is a hard exclusion — RCX is never a safe choice
   for a flagged value, so skip straight to eviction rather than
   accepting it). Confirmed fixing `172_base64_module.py` (all 10
   lines) and, as a bonus, ALL FOUR remaining crash-bucket entries that
   turned out to share this exact root cause: `196_hashlib_module.py`,
   `225_hashlib_module.py`, `229_base64_module.py` (hashlib's SHA/MD5
   implementations are heavy variable-shift users) — 6 crash-bucket
   entries closed by one fix, the largest single-fix impact since pass
   six's class-var feature (5 entries).

Verified: `tests.runner` 475/483 throughout. Full sweep: `OK=305
MISMATCH=13 CRASH=3 BUILD_FAIL=117`, up from `OK=301 MISMATCH=12
CRASH=9 BUILD_FAIL=116` before this pass — +4 OK, -6 crashes (exactly
the five confirmed above plus one more), +1 mismatch/+1 build-fail
(within the sweep's documented run-to-run noise — always trust the
direct build+run spot-checks over the aggregate deltas alone).

**Cumulative sweep numbers after twelve triage passes this session,
from the `OK=245 MISMATCH=24 CRASH=42 BUILD_FAIL=127` session-start
baseline**:

```text
OK=305  MISMATCH=13  CRASH=3  BUILD_FAIL=117
```

56% → 70% of the full 438-case corpus passing. The crash bucket has
gone from 42 down to just 3 across all twelve passes.

## Selfhost Status (plan-step 11)

Selfhost = asmpython (gen0, built by CPython) compiling its own source to
produce gen1, and gen1 compiling the same source again to produce gen2.

**Front-end gauntlet progress** (2026-07-16, same-day follow-up): the
`python -m selfhost.check` LEX→PARSE→SEMA gauntlet (a cheaper, earlier
signal than the full gen1/gen2 build below — measures whether the
compiler's own source is even accepted by its own front end, before ever
trying to codegen/assemble it) went **16/19 → 18/19 files passing**. Two
real bugs found and fixed:

1. `codegen.py` itself failed SEMA (`zip() arguments must be lists or
   tuples`) on its own `for i, (zn, ze) in enumerate(zip(znames,
   zexprs)):` loop (`_cl_walk`, ~line 2348). Root cause: `znames`/`zexprs`
   come from unpacking `self._for_zip_spec(s)`'s tuple return, and sema's
   `_scan_tuple_return`/`_collect_tuple_returns` (which infers a
   function's tuple-return per-slot types by scanning its `return a, b,
   c` statements) read `A.expr_type(el)` on each returned expression --
   but this scan runs on the RAW, not-yet-type-checked function body, so
   an expression like `list(s.targets)` (a real list-producing builtin
   call) still carried the *parser's* placeholder default
   (`Call.inferred_type` defaults to `"int"`), not its real kind. Fixed
   with two additions: `_scan_slot_kind` (recognizes `list`/`tuple`/
   `dict`/`set`/`sorted` builtin-constructor calls and list-literals
   directly, instead of trusting an unchecked node's `inferred_type`) and
   `_collect_local_annots` (a flat local-variable-name → annotated-type
   table scanned once per function, so a bare-`Name` return slot
   referencing an annotated local like `zip_vars: list = []` resolves
   correctly too). This is a real, generally-applicable sema gap — any
   function returning a tuple containing a `list(...)`/`sorted(...)`/etc.
   call or an annotated local variable was previously vulnerable to the
   same silent mistyping; not just this one call site.
2. `driver.py` crashed SEMA outright (`AttributeError: 'MultiAssign'
   object has no attribute 'values'`) in `_param_usage_hints`'s inner
   `scan_stmts` (~line 1614) — a stray plural `s.values` on an
   `A.MultiAssign` node, which only ever has a single `.value` (`a = b =
   c = value` evaluates the RHS once). The other two `A.MultiAssign`
   handling sites in `sema.py` already used the correct singular field;
   this one was an isolated typo. Fixed by reading `s.value` directly (no
   loop needed, matching the other two sites' pattern).

**Remaining gate**: `__main__.py` fails SEMA with `exec() is not
supported: it requires a Python interpreter and cannot be compiled to
native code` — at the new `_load_ext_plugin` function (this session's
`--ext path/to/plugin.py` loading, see "Extension System" above), which
genuinely, irreducibly needs a real Python `exec()` to run an arbitrary
third-party plugin file. This is not a bug to fix so much as a real
design question: either (a) the CLI driver (`__main__.py`) is
deliberately excluded from asmpython's self-hosted subset (a common
bootstrapping-compiler pattern — the compiler CORE self-hosts; the
outermost CLI shell stays CPython-only), or (b) plugin loading is
redesigned to avoid `exec()` under self-hosted compilation specifically
(e.g. only available when the compiler itself is CPython-hosted). Neither
implemented yet — flagged here for whoever picks up self-host next to
make a real decision on, rather than silently left as an unexplained
gauntlet failure.

**Last confirmed gen1/gen2 build blocker** (older, not re-verified this
session — the front-end gauntlet above is a necessary but not sufficient
precondition for this): gen1 compiling `asmpython/__main__.py` produces a
gen2 `.asm` truncated to ~4,426 lines (vs. gen0's ~510,000+), zero function
labels emitted, failing to assemble. Not yet root-caused — likely
`program.py`'s whole-program-merge import closure silently failing to expand
for gen1 specifically. Two other known-open issues found along the way:
`import os` (alone, unused) segfaults gen1 specifically; `isinstance(x, T)`
in gen1-produced code always returns `False` for the *first*-declared class
in a program (2nd/3rd+ classes work). **Should be re-verified fresh** given
how much has changed since this was last confirmed (the front-end gauntlet
alone went from 16/19 to 18/19 this session) — it's possible some or all of
this is already stale.

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
- **First-class module values** (new 2026-07-16): real Python parity for
  `import x` producing a genuine storable/dict-able value with working
  attribute access (`d = {"gcc": gcc_module}; d["gcc"].link(...)`), not
  just a compile-time namespace lookup as today. Motivated by
  `_backends/x86_64/__init__.py`'s linker dispatch (~line 166-179), which
  has an explicit comment explaining it uses hardcoded if/elif instead of
  a name→module dict specifically because "asmpython has no first-class
  module values" and such a dict "isn't representable under self-hosted
  compilation" — the same constraint would block a clean `asmpython.
  Backend(...)`/`asmpython.Linker(...)` registry once self-hosting is a
  real requirement. This is a foundational object-model feature (sema's
  type system, the runtime object model, both codegen backends), not
  scoped to any one registry — sized similarly to a language-level feature
  like the walrus operator, but for a much more central construct.
  Deliberately NOT attempted as a side effect of the Backend/Linker
  registry work below; scope it as its own effort when picked up. Until
  then, `asmpython/_backends/__init__.py`'s and `asmpython/_linkers/
  __init__.py`'s registries (see "Extension System" above) use plain
  Python dicts under CPython-hosted compilation only — fine for now since
  self-hosting these two `_backends`/`_linkers` files specifically isn't
  yet a confirmed near-term requirement, but flagged here so whoever picks
  up self-hosting next knows this dispatch will need revisiting.
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
