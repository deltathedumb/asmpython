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

**Next step on resume**: two reasonable directions, pick based on what's
more valuable to unblock next:

1. Clear the remaining scattered 1-60 build failures (small, independent
   fixes, same pattern as most of this session — likely a few hours
   total) to push the smoke-test corpus even closer to 60/60.
2. Given the "Everything Python" bar (run essentially any unmodified
   real-world Python program, native-first with pyinbin fallback), pivot
   to validating against **real-world Python programs** beyond this
   project's own hand-written test corpus — the corpus is necessarily
   narrow (~440 hand-authored cases) and passing it doesn't guarantee
   broad real-code compatibility. Consider running a batch of actual
   PyPI-package-free stdlib-only scripts (or CPython's own `Lib/test/`
   suite, already used as pyinbin's conformance oracle per the pyinbin
   section below) through `--backend x86-64` to find the next tier of
   gaps a hand-written smoke corpus wouldn't surface.

Either way, this is still all in service of the confirmed work order's
"finish the IR-based x86-64 backend and make it the default" pending
item — parity work doesn't stop being valuable just because the
smoke-test corpus is clean, since that corpus was never meant to be
the definition of "done."

**When new Win64 ABI shims are added going forward, verify stack-slot
placement (must be at/above rsp+32) and argument-register assignment
(shared positional index, not per-type) explicitly — both bug classes
found this session assembled cleanly and only failed at runtime,
sometimes on a delayed/second call.**

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
