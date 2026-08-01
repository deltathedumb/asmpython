# Failing-case audit, by root cause

All **330** failing cases at the pinned tree, ordered by root cause. A cause is
listed only where it was established, not inferred from the symptom.

`verified` means reproduced at head with a minimal probe that was actually run.
`strong` means the symptom is diagnostic but the case was not individually
reduced. **NOT root-caused** means exactly that — the cases are grouped by where
they fail, because attributing them would be guessing.

## What tree this is

| | |
|---|---|
| corpus | 1143 cases, 813 passing, **330 failing** (`tests/baseline.json`) |
| baseline recorded at | `821ceb9c` (dirty) |
| this audit measured at | `450068a5`, in a dedicated `git worktree` |
| method | every case compiled with `--no-pyinbin-fallback`, IR dumped via `ASMPYTHON_EMIT_IR`, binary run, stdout diffed against its `# expect:` block |

`--no-pyinbin-fallback` is essential and is **not** what the baseline runner
uses. The fallback is on by default (`_compiler/__main__.py:1258`), so a case the
native backend refuses is silently executed by the interpreter instead. At the
CLI those programs look like they work.

**98 of the 330 are native compile refusals.** A large share of them are
invisible without the flag: the fallback runs the program, the compiler exits 0,
no artifact is written, and the corpus reports the missing file as
`runner error: [WinError 2]` rather than as a compiler diagnostic.

The checked-in `results.txt` shows the same split from the other direction — 56
`runner error: [WinError 2]` and 42 `compile failed:`, which sum to 98. Treat
that as corroboration rather than a second exact measurement: `results.txt` was
written at a nearby but not identical tree state (it reports 821/1144, against
this audit's 813/1143), so the totals are close but the runs are not the same.

> **Caveat on freshness.** `00955558` ("ir_lower/sema: boxed-return convention
> for callable-valued functions") landed while this audit was running and is
> **not** reflected in it. It targets exactly the indirect-call arm of §1
> (`indirect_bare` / `indirect_ann`), so any case attributed to that arm should
> be re-measured before acting on it. Nothing else here is affected: the sweep,
> the matrices and the origin comparison were all taken at `450068a5`.

## How each case was classified — mechanically, before any attribution

Counts below are facts from the sweep, not judgements:

| observation | cases |
|---|---:|
| wrong output (compiled and ran, stdout ≠ expected) | 165 |
| native compile refused | 98 |
| crashed at run time | 65 |
| hung (>30s) | 1 |
| no `# expect:` block in the case at all | 1 |
| **total** | **330** |

Two of these had no counterpart in the previous audit at all:

- **65 crashes.** The old audit had no crash category; `PHASE0.md` named three
  segfaults and called them "the highest-severity items in the corpus".
  There are **55 access violations (`0xC0000005`)**, 9 run-time exceptions
  escaping as exit 1, and one stack overflow (`0xC00000FD`).
- **98 compile refusals**, against 31 in the old audit — because the old
  measurement did not disable the interpreter fallback.

---

## 0. The previous audit was taken on a branch that was never merged

**This is the largest single finding, and it is not a compiler defect.**

`FAILURE_AUDIT.md` was pinned at `5a9355ad`. That commit **is not an ancestor of
the local branch**:

```text
$ git merge-base --is-ancestor 5a9355ad HEAD
  -> false
$ git merge-base 5a9355ad HEAD
  ae1c6144   "Carry set/dict element kinds and decode stored keys on read"

$ git rev-list --count origin/beta/3.14.0..beta/3.14.0     # local, not on origin
  17
$ git rev-list --count beta/3.14.0..origin/beta/3.14.0     # on origin, not local
  23
```

Local `beta/3.14.0` and `origin/beta/3.14.0` diverged at `ae1c6144`. The audit's
commit is on the **origin** side. So the audit was never "34 cases stale" in the
sense of a regression — it was measured on a different line of development, and
23 commits of language work on that line have never reached the local branch:

```text
bb54e509 Infer a parameter through forwarding and through calls; lex semicolons
ca598dff Type a parameter from its default, and floor float modulo
5a9355ad Keep all()/any()/set()'s sequence and bound in slots too        <- audit pinned here
...
55929809 Parse @ as matrix multiplication, parenthesized with-items, and every filter
327b1965 Support multiple comprehension if-clauses and constant arithmetic defaults
af876c65 Support nested unpacking, in assignments and loop targets
d8a16d5d Callable returned functions, the ascii builtin, chained subscript assignment
edf0fe4b Bind dotted imports, so `import os.path` stops segfaulting
c414ca92 Call __index__ when an object is used as an index
2354ba65 Dispatch __hash__ for instance dict keys
088e92af Implement set symmetric difference, in both spellings
```

**34 of the current 330 failures are cases that pass at the origin tip.** This
was measured, not inferred from the commit subjects — all 34 were built and run
at `bb54e509`:

```text
34/34 PASS
```

| | |
|---|---|
| cause | 23 commits of frontend/inference work exist only on `origin/beta/3.14.0` |
| confidence | **verified** (34/34 built and run at `bb54e509`) |
| cases | **34** |
| fix | merge or cherry-pick `origin/beta/3.14.0` |

The 34: `algo_dfs_recursive`, `algo_prime_sieve`, `app_template_render`,
`ascii_builtin`, `callable_as_default_arg`, `compose_functions`,
`conversion_int_index_context`, `default_arg_expression`,
`dunder_eq_hash_dict_key`, `dunder_index`, `find_duplicates`,
`function_returning_function`, `generator_in_join`, `indented_tree`,
`int_prog_validator`, `list_comprehension_two_cond`, `list_index_of_tuple`,
`literal_float_forms`, `matmul_operator`, `multiple_assignment_targets`,
`nested_tuple_unpack`, `nested_unpacking_for`, `ospath_basename`,
`ospath_dirname`, `ospath_join`, `parenthesized_context_managers`,
`prog_filter_chain`, `r39_dict_invert_multi`, `r39_flatten_dict`,
`r39_url_builder`, `r40_gradient`, `set_symmetric_difference`,
`sim_grade_report`, `sim_matrix_stats`.

None of the 34 case files was edited since `5a9355ad`, so these are not
regenerated expectations — the compiler on the local line genuinely cannot
compile programs the origin line can.

### How much does merging actually recover? Measured exhaustively: 38

Every one of the 330 failing cases was built and run at `bb54e509` — the 34
above, then a stratified sample of 80, then the remaining 215, then
`syntax_semicolons.py` individually. That is 330 of 330; nothing is
extrapolated.

| measured at `bb54e509` | passes |
|---|---:|
| the 34 above | 34 |
| stratified sample of 80 | 1 — `crash_float_modulo_negative` |
| remaining 215 | 2 — `algo_merge_sort`, `crash_float_default_param` |
| `syntax_semicolons` (checked individually) | 1 |
| **total** | **38** |

**Merging recovers exactly 38 of 330 and no more.** The four beyond the original
34 are cases that were failing at `5a9355ad` too, and were fixed by the two
commits that came after it (`ca598dff` types a parameter from its default and
floors float modulo; `bb54e509` lexes semicolons).

So the gap is worth 38 cases — substantial, and free of compiler work — but it is
**not** a broad fix for the remaining 292. Cause 1 explains why precisely: the
unmerged work closes *half* of the parameter-inference boundary, and real corpus
programs need the half that stays open.

---

## 1. A value's kind is lost at any unannotated parameter boundary that is not fed a literal

This is the defect the previous audit was pointing at when it attributed ~84
cases to "the type lattice". It is now measured exactly, and the measurement
changes the picture in two ways: **containers are not the problem**, and the
same single defect produces five unrelated-looking symptoms.

### The boundary, mapped

46 storage/retrieval sites were probed with the value (a `str`) and the
observation (`print`) held fixed, so the only variable is the site.
**39 of 46 preserved it.** Every container site probed — bare list, `list[str]`,
`list[object]`, list literal, heterogeneous list, iteration, `pop`, slice,
nested list, dict value/literal/heterogeneous/nested/`get`, tuple literal,
tuple unpack, tuple-in-list, set member — **round-trips correctly**.

> **Correction to that claim, found while auditing it.** "Containers are fine"
> holds only at nesting depth ≤ 2, which is all the matrix probed. The
> element-kind encoding is depth-limited, and the third level is a raw pointer:
>
> ```text
> {'a': 1}                  -> {'a': 1}                   ok
> {'a': {'b': 1}}           -> {'a': {'b': 1}}            ok
> {'a': {'b': {'c': 1}}}    -> {'a': {'b': 8532416}}      BREAKS at depth 3
> [[1]]                     -> [[1]]                      ok
> [[[1]]]                   -> [[8556272]]                BREAKS at depth 3
> ```
>
> So containers are not the *primary* site — the parameter boundary below is —
> but they are not blameless either. This is a separate, narrower defect
> (`_value_repr_kind` encodes only two levels) and it is what
> `repr_nested_dict` and `deeply_nested_comprehension` actually hit.

A second matrix varied only *how the parameter's type would have to be inferred*:

| probe | source | result |
|---|---|---|
| `arg_literal` | `f('abc')` | **ok** |
| `arg_local_var` | `x = 'abc'; f(x)` | **raw pointer** |
| `arg_list_elem` | `xs = ['abc']; f(xs[0])` | **raw pointer** |
| `arg_call_result` | `f(src())` | **raw pointer** |
| `arg_field` | `f(C().t)` | **raw pointer** |
| `hop1_forward` | `def f(s): g(s)` | **raw pointer** |
| `hop1_inner_ann` | `def g(s: str)` | ok |
| `hop1_outer_ann` | `def f(s: str): g(s)` | **raw pointer** |
| `ret_attr_of_param` | `def get(o): return o.t` | **raw pointer** |
| `ret_attr_param_ann` | `def get(o: C): return o.t` | ok |
| `indirect_bare` / `indirect_ann` | `fn(a)` through a parameter | **raw pointer** (both) |

> **The rule: an unannotated parameter keeps its type only when a literal is
> passed directly at the call site.** A local variable holding that same literal
> is already enough to lose it. Annotating the *caller* does not help; only
> annotating the parameter that is read does.

The four-line repro, and its IR, make the mechanism unambiguous:

```python
def f(s):
    print(s)
x = 'abc'
f(x)
# CPython:   abc
# asmpython: 5368737797
```

```text
func f(%arg_s: i64) -> i64 {                     <- typed i64, not ptr
    %t3: i64 = load %t2
    %t6: ptr = call _abi_int_to_base, %t3, ...   <- printed as a DECIMAL
```

against the identical program with the literal inlined:

```text
func f(%arg_s: ptr) -> i64 {                     <- typed ptr
    call printf, %t6, %t5                        <- printed as "%s"
```

Nothing else differs — same two functions, same string constant. The failing
program's IR signals are `_abi_new_box=0  anyunbox=0`: it does not box the value
and it does not tag it, it simply reinterprets a `str` pointer as an integer.

### Why `int` survives and nothing else does

```python
def g(s): print(s)
def f(s): g(s)
f(7)      # -> 7        ok
f(1.5)    # -> 3969024  raw pointer
f([1, 2]) # -> 11281264 raw pointer
```

An `int` passes through the unknown sentinel unharmed **because the sentinel is
represented as `int`**. Every other kind is corrupted. This is exactly the
conflation `PHASE1.md` names, demonstrated end to end in four lines.

### One defect, five symptoms — this is why the corpus looks like five problems

Holding the program shape fixed and changing only what is done with the value:

```python
def g(s):
    <USE>
def f(s):
    g(s)
f('abc')
```

| `<USE>` | result | which triage bucket it lands in |
|---|---|---|
| `print(s)` | `5368737797` | wrong output — raw pointer |
| `print(s.upper())` | `0` | wrong output — "resolves to 0/None" |
| `print(len(s))` | raw pointer | wrong output — numeric |
| `print(s[0])` | **CRASH `0xC0000005`** | run-time crash |
| `print(s + '!')` | **compile refusal `[E012]`** | compile-time refusal |

**A single lost kind is therefore reported by the corpus as four different
failure classes.** Any audit that groups by symptom will split this cause across
four buckets — which is what the previous one did.

There is a fifth disguise, and it is the least obvious. When the lost value
lands in a slot typed `float`, the raw pointer is **reinterpreted as an IEEE754
double**, which prints as a denormal near `1e-317`:

```text
observed in the corpus   1.951741e-317   3.177198e-317   3.861206e-317   2.23505654e-316
```

Those are not "float formatting" defects, which is where a symptom-based reading
puts them. Reinterpreting the pointer values this compiler actually hands out
reproduces the band exactly:

```text
pointer     3969024  as a double = 1.9609584e-317
pointer     8135568  as a double = 4.0195047e-317
pointer    11281264  as a double = 5.5736850e-317
```

`3969024` is the literal value the `KIND_fwd_float` probe printed. So
`crash_float_default_param` (`3.177198e-317`), `crash_nested_function_float`
(`1.951741e-317`) and `min_mixed_int_float` (`1.5e-323`) are this cause, not a
formatting cause — and `crash_float_default_param` is confirmed to pass at the
origin tip, where `ca598dff` types a parameter from its default.

The `0` arm is its own verified sub-defect: the value there *is* boxed
(`_abi_new_box=1`, 90 `anyunbox` blocks), but the `any`-typed method-dispatch
chain has no arm for `str` methods, so it falls through to `0`:

```text
s.upper()          -> 0
s.lower()          -> 0
s.strip()          -> 0
s.split(',')       -> 0
s.replace('a','b') -> 0
```

### What the unmerged branch does and does not fix

The same matrix run against `bb54e509`:

| | local `450068a5` | origin `bb54e509` |
|---|---:|---:|
| inference sites preserved | 11/25 | **18/25** |

Fixed by the unmerged work: `hop1_forward`, `hop1_outer_ann`, `hop2_forward`,
`arg_local_var`, `ret_of_forwarded`, and the `float`/`list` kinds through
forwarding.

**Still broken at origin:** `arg_list_elem`, `arg_call_result`, `arg_field`,
`ret_attr_of_param`, `indirect_bare`, `indirect_ann`, `comp_call_unann`.

That is the precise reason merging recovers only ~35 cases: real corpus programs
pass list elements, call results and fields, not bare locals.

### How many cases does this explain?

**Deliberately not asserted from the mechanism.** A static screen (real `ast`,
`attribute.py`) for the boundary shape being present *in the case's own source*
finds it in **62 of 330** cases. That is an upper bound on the directly-visible
instances and an undercount of the true figure, because the `asmpython/stdlib/*.py`
shims are themselves written in the annotation-light subset, so a case can hit
the boundary inside a shim while its own source is clean — which is why the
stdlib partition screens at 4% while its symptoms match.

Per-case attribution is in the cause table below, and cases that could not be
attributed are listed as such rather than swept in.

---

## 2. Number and string formatting: eleven distinct defects, measured

The previous audit carried "number formatting" (6, unattributed), "f-string:
nested format spec" (8), "f-string: percent format spec" (2) and
"round(x, n)" (5) as four separate entries. A 41-probe conformance matrix — every
expectation computed by executing the probe under the host CPython 3.14, so a
wrong expectation fails at generation rather than becoming a bogus finding —
resolves them into the following. **19 of 41 conform; 22 do not.**

| # | defect | probe evidence | confidence |
|---|---|---|---|
| 2a | **C 3-digit exponent.** msvcrt writes `e+004`; CPython writes `e+04`. Affects `:e`, `:E`, `:.Ne`, `:g` both directions, and `%e`. | `f'{12345.678:e}'` → `1.234568e+004`, want `1.234568e+04` | verified |
| 2b | **`%` format spec unimplemented** — the raw float is printed. | `f'{0.25:%}'` → `0.25`, want `25.000000%` | verified |
| 2c | **Nested/dynamic format specs unimplemented** — the spec text is emitted literally. | `f'{3.14159:{w}.2f}'` → `{w}.2f`; `'{:.{}f}'.format(3.14159, 2)` → `3.14159f}` | verified |
| 2d | **`round(x, n)` scales in binary.** `2.55*10` is exactly `25.5`, ties-to-even gives `26`. | `round(2.55, 1)` → `2.6`, want `2.5`; `round(2.675, 2)` → `2.68`, want `2.67` | verified |
| 2e | **`round(x, negative)` returns float.** | `round(12345, -2)` → `12300.0`, want `12300` | verified |
| 2f | **float `%` uses C `fmod`, not Python's floored modulo.** | `-5.5 % 2.0` → `-1.5`, want `0.5` | verified |
| 2g | **`=` alignment ignored.** | `f'{42:=+8}'` → `42`, want `+     42` | verified |
| 2h | **`_` grouping unimplemented for non-decimal bases.** | `f'{255:_b}'` → `11111111`, want `1111_1111` | verified |
| 2i | **`f'{x=}'` debug spec is a parse error.** | `unexpected tokens in f-string expression: 'x='` | verified |
| 2j | **`None` inside a container renders as `0`.** | `print([1, None])` → `[1, 0]` | verified |
| 2k | **`repr` of a str never switches quote style.** | `["it's"]` → `['it's']`, want `["it's"]` | verified |

Two things the previous audit listed here are **no longer true** and should not
be carried forward: `round(2.7)` returns an `int` (`round_returns_int`,
`round_type` both conform), and `print([True, False])` renders `[True, False]`
correctly. `repr_bool_in_list` has left the failing set.

`2d` and `2e` are one component away from each other: both need exact decimal
conversion. `PHASE1.md` §3 establishes msvcrt cannot supply it (it rounds
half-away-from-zero and zero-fills past 17 significant digits), so a real `dtoa`
in the runtime is the prerequisite for both, and for `2a`.

---

## 3. Causes verified directly in this session

Each was reproduced with a probe run at `450068a5`. Where a partition agent
reached the same conclusion independently, that is noted — two methods agreeing
is stronger than either alone.

### 3a. `str` is a UTF-8 byte string, not a sequence of code points — *verified*

```python
s = 'héllo'
print(len(s))      # asmpython 6      CPython 5
print(ord('中'))   # asmpython 228    CPython 20013     (228 = 0xE4, the first UTF-8 byte)
print(s[1])        # asmpython a single byte, rendered as U+FFFD
print('中'.upper())# asmpython 中     CPython 中        (correct by accident: ASCII-only casing)
```

`len`, `ord`, indexing and slicing all operate on bytes. Independently reached
by the crash-partition agent from the opposite direction, via
`json.dumps(ensure_ascii=True)` emitting `Ã©` — the two UTF-8 bytes of
`é` escaped separately.

Explains at least: `str_unicode_len` (6/5), `unicode_emoji_len` (6/3),
`unicode_ord_high` (228/20013), `unicode_upper_accent`, `unicode_in_list_repr`,
and the mojibake in `462_json_dumps_options`.

### 3b. Tuple comparison stops at element 0 — *verified*

```python
print(sorted([(1, 2), (0, 9), (1, 1)]))
# asmpython [(0, 9), (1, 2), (1, 1)]
# CPython   [(0, 9), (1, 1), (1, 2)]
```

Ties on the first element are left in input order instead of being broken by the
remaining elements. Everything adjacent is **correct** and was checked in the
same probe: `key=len`, `key=lambda`, `reverse=True`, float ordering, and sort
stability. So this is specifically the tuple comparator, not the sort.

Explains `sorted_tuples_multi` — which the previous audit listed as *NOT
root-caused* — and `sorted_with_two_keys`.

### 3c. `sorted()` ignores `__lt__` on instances — *verified*

```python
class P:
    def __init__(self, v): self.v = v
    def __lt__(self, o):   return self.v < o.v
print([p.v for p in sorted([P(3), P(1), P(2)])])
# asmpython [3, 1, 2]   (input order — the comparator is never consulted)
# CPython   [1, 2, 3]
```

### 3d. An unhandled exception is written to **stdout** — *verified*

```text
$ prog.exe 2>/dev/null          $ prog.exe 2>&1 >/dev/null
before                          (empty)
Unhandled exception: boom
(exit 1)
```

CPython writes the traceback to **stderr**. Here it goes to stdout, so it is
captured as program output and the case fails on the extra line even when the
exception itself is correct behaviour. The exit code (1) is right; the stream is
not. Independently found by the crash-partition agent, which attributed 5 cases
to it.

This one is cheap to fix and makes 9 `exit 1` cases legible, because right now
the real failure is hidden behind a stdout diff.

### 3e. `[1, 2, 3][::0]` hangs forever instead of raising `ValueError` — *verified*

The single `RUN-TIMEOUT` in the corpus (`slice_step_zero_error.py`, >30s). The
case file documents it in its own trailing comment. The crash-partition agent
located the mechanism: `_runtime_list_slice_step` advances its cursor by the
step with no `step == 0` guard anywhere in sema, lowering, or the runtime.

Severity note: this is a denial-of-service reachable from ordinary Python, and
it is the only case in the corpus that never terminates.

### 3f. `462_json_dumps_options.py` has no `# expect:` block — *verified*

A genuine corpus defect: `runner._parse_expect` returns `None` and the case can
never pass. Distinct from the audit's other "corpus defect" entries, which are
about wrong expectations rather than absent ones.

---

## The causes, ranked

132 causes over 289 of the 330 cases. 41 are **NOT root-caused** and are listed as such at the end.

A case appears under exactly one cause. Where more than one cause claimed a case (42 of them), it was assigned to the claim with the higher confidence, then to the one carrying a reproduction. Counts are recomputed from the surviving case lists, never taken from a claim.

> **Confidence was not adversarially re-tested.** The verification pass that was supposed to try to refute every cause claiming more than six cases did not run — the session hit its limit after the finding pass. `verified` below therefore means *the analysis that produced it ran a probe and reported the output*, not *a second, hostile analysis failed to break it*. The causes marked `verified` in §0–§3 above are the exception: those were reproduced directly.

### Causes explaining 2 or more cases

| rank | root cause | confidence | cases |
|---|---|---|---|
| 1 | Frontend/inference work that exists only on `origin/beta/3.14.0` is absent locally | verified **>10** | 38 |
| 2 | Python-source stdlib shims declare hand-narrowed signatures while their implementations are correct and complete; sema faithfully enforces the narrowed signature and refuses the CPython spelling | verified | 8 |
| 3 | Attribute access lowers to `_abi_dict_get_default(recv, name, 0)` with no check that the receiver is a heap instance; the any-tag guard is emitted on the call's RESULT instead | verified | 8 |
| 4 | An `any`-element container is read with TAGGED_REPR_KIND (6), which requires a BOX_MAGIC header, but the matching `_abi_list_append` write stores the value raw — the magic check fails and the raw pointer is printed in decimal | verified | 7 |
| 5 | shims that read as complete implement a materially different algorithm, so they return well-typed wrong data with no stub marker to find them by | verified | 5 |
| 6 | An unmodelled call is silently lowered to `const 0`, and the next operation uses that 0 as a live heap pointer | verified | 5 |
| 7 | sema's `_collect_returns` never descends into `A.Match` case bodies, so a function whose only `return`s live inside a `match` is declared `-> i64` | verified | 5 |
| 8 | `str` is a UTF-8 byte string, not a sequence of code points | verified | 5 |
| 9 | Genuinely unimplemented stdlib callables, not lost receiver kinds: the math binding table is scalar-only and time has no struct_time record type | strong | 5 |
| 10 | Frontend productions are fixed-shape: clause lists, nested lambdas, yield-expressions and f-string specs are hard parse errors | verified | 4 |
| 11 | An attribute-lookup miss returns the dict-default sentinel 0, which is then dereferenced as an object or called as a function pointer | verified | 4 |
| 12 | The assignment/deletion target grammar is a strict subset of the rvalue grammar: strided slice stores and attribute/slice del targets are unimplemented | verified | 4 |
| 13 | 3-arg type(name, bases, ns) is unimplemented, so the namedtuple shim cannot construct its class and every namedtuple call is refused | verified | 4 |
| 14 | When `_infer_return_type` cannot type a function's returns it falls back to `i64` rather than to `any`, so every returned pointer/float/tuple-element is formatted as a decimal at the call site | verified | 4 |
| 15 | A refused statement leaves its target unbound, so every later use emits a spurious cascade E001 that inflates the 'undefined name' bucket | verified | 4 |
| 16 | `bytes` and `bytearray` are the static type `list`, so they render as a list of ints | verified | 4 |
| 17 | Nested / dynamic format specs are emitted literally instead of being evaluated | verified | 4 |
| 18 | Stdlib objects with no distinct static type are represented by a surrogate primitive, so every method that exists only on the real type is refused | verified | 3 |
| 19 | `list_el_type` is a side-channel AST attribute; a module-global binding never carries it, so globals silently default to `int` element kind | verified | 3 |
| 20 | marshal / reprlib / unicodedata ship no bundled source at all; the new E005 refusal replaced an older silent miscompile, so these cases' recorded symptoms are stale | verified | 3 |
| 21 | Features implemented only as compile-time expansion have no runtime fallback, so a non-literal or non-inlinable operand is refused outright | verified | 3 |
| 22 | The repr-kind word encodes only two levels of container nesting: `_value_repr_kind` returns 0 (int) for `list`/`dict`/`tuple`, so anything nested three deep renders its innermost cells as decimal pointers | verified | 3 |
| 23 | A `lambda` body is name-resolved against globals + its own params only; the enclosing function's scope is not on its lookup chain | verified | 3 |
| 24 | The runtime's top-level exception handler writes "Unhandled exception: <msg>" to STDOUT with no exception type and no traceback, and exits 1 — so every escaping Python exception silently corrupts the case's stdout | verified | 3 |
| 25 | list.append on a parameter-bound receiver emits no _abi_list_append at all; the mutation is silently dropped | verified | 3 |
| 26 | self-documented placeholder shims return a different object than the API promises, so callers silently compute on the wrong thing | verified | 3 |
| 27 | Closure conversion drops a variable captured from a grandparent scope, substituting a load from an uninitialized alloca, and indirect call sites are emitted with the source-level arity instead of the lifted arity | verified | 3 |
| 28 | an indirect call through a function-valued name returns garbage instead of the callee's result | verified | 3 |
| 29 | The element-kind tag is depth-1 and non-recursive: a container records its element's kind but not that element's own element/value kind | verified | 3 |
| 30 | A container built by statements inside a function loses its element/value kind at the return boundary, so the caller reads elements at the default `int` kind | verified | 3 |
| 31 | 11 failing cases have expect blocks that CPython itself does not satisfy, so they cannot pass regardless of compiler correctness | verified | 3 |
| 32 | stdlib predicate shims are declared `-> int` and return literal 1/0, so every bool-valued stdlib result prints as 1/0 | verified | 3 |
| 33 | `except ... as e` binds e to the raise-site message string, not an exception object, so type(e) and user __str__ are unreachable | verified | 3 |
| 34 | the random module binds to the C runtime LCG (srand/rand), not the Mersenne Twister, so every seeded-random expectation is unreachable by construction | verified | 3 |
| 35 | `None` stored in a container renders as `0` | verified | 3 |
| 36 | C's 3-digit exponent: `e+004` where CPython writes `e+04` | verified | 3 |
| 37 | The `%` format-spec presentation type is unimplemented; the raw float is printed | verified | 3 |
| 38 | A function reached only as a first-class value gets no argument types, so its unannotated params and its call result both fall back to `int` | verified | 2 |
| 39 | ordering builtins never perform structured comparison: sorted() lowers to _abi_sort_int over raw pointers, and min() over tuples returns the first item unexamined | verified | 2 |
| 40 | instance attribute lookup reads a fixed slot and never consults __getattr__ or the data-descriptor protocol, yielding const 0 on a miss | verified | 2 |
| 41 | the is_bool side-channel flag does not survive tuple-unpacking assignment or a classmethod return, so True/False render as 1/0 | verified | 2 |
| 42 | shim return annotations state the wrong Python type, and the compiler faithfully materialises the declared type instead of the value's own | verified | 2 |
| 43 | A refused call still binds its target name as undefined, emitting a cascading E001 that inflates the triage code counts | verified | 2 |
| 44 | The binop checker has explicit arms for `list + list` and `list * int` but none for tuple, so both fall through to the numeric-only reject | verified | 2 |
| 45 | `functools.reduce`'s shim types its callable parameter as `int` and its accumulator as `object`; the value returned across the indirect call comes back boxed and the caller reads it as an int, printing the box address | verified | 2 |
| 46 | A direct call passes a raw i64 into a parameter the callee declares as `ptr`; the IR permits the i64->ptr narrowing silently, so the callee's first dereference of that parameter faults | verified | 2 |
| 47 | A float read back through an `int`-kinded container slot is printed with the integer formatter, emitting its raw IEEE-754 bit pattern as a decimal | verified | 2 |
| 48 | `bytes` has no distinct static type -- it is list[int], so bytes values repr as a list of ints | verified | 2 |
| 49 | The `_` grouping flag in a format spec is ignored for the binary presentation type, because `_abi_int_to_binary` takes no grouping argument | verified | 2 |
| 50 | `round(x, n)` scales in binary, so exact-tie cases round the wrong way | verified | 2 |
| 51 | Tuple comparison stops at element 0 and never breaks ties on later elements | verified | 2 |
| 52 | `int` is a wrapping 64-bit integer; there is no arbitrary-precision path | verified | 2 |
| 53 | Three independent formatting/introspection builtins are unimplemented, each refusing at its own closed-set dispatch site | strong | 2 |

Plus **79** causes explaining a single case each, tabulated after the detail sections.

---

### 1. Frontend/inference work that exists only on `origin/beta/3.14.0` is absent locally  (38)  — *verified*

The local branch diverged from origin at `ae1c6144` and is 23 commits behind. Those commits carry the `@` operator, chained assignment, nested unpacking, parenthesized with-items, multiple comprehension if-clauses, dotted imports, `__index__`, `__hash__` dispatch, set symmetric difference, the `ascii` builtin, semicolon lexing, and parameter inference through forwarding. None of it is a defect in the local compiler; it is work that was never merged.

Evidence:

```text
All 330 failing cases were built and run at bb54e509. 38 pass there.
```

Minimal repro:

```python
git rev-list --count beta/3.14.0..origin/beta/3.14.0   ->  23
```

Cases: `algo_dfs_recursive.py`, `algo_merge_sort.py`, `algo_prime_sieve.py`, `app_template_render.py`, `ascii_builtin.py`, `callable_as_default_arg.py`, `compose_functions.py`, `conversion_int_index_context.py`, `crash_float_default_param.py`, `crash_float_modulo_negative.py`, `default_arg_expression.py`, `dunder_eq_hash_dict_key.py`, `dunder_index.py`, `find_duplicates.py`, `function_returning_function.py`, `generator_in_join.py`, `indented_tree.py`, `int_prog_validator.py`, `list_comprehension_two_cond.py`, `list_index_of_tuple.py`, `literal_float_forms.py`, `matmul_operator.py`, `multiple_assignment_targets.py`, `nested_tuple_unpack.py`, `nested_unpacking_for.py`, `ospath_basename.py`, `ospath_dirname.py`, `ospath_join.py`, `parenthesized_context_managers.py`, `prog_filter_chain.py`, `r39_dict_invert_multi.py`, `r39_flatten_dict.py`, `r39_url_builder.py`, `r40_gradient.py`, `set_symmetric_difference.py`, `sim_grade_report.py`, `sim_matrix_stats.py`, `syntax_semicolons.py`

### 2. Python-source stdlib shims declare hand-narrowed signatures while their implementations are correct and complete; sema faithfully enforces the narrowed signature and refuses the CPython spelling  (8)  — *verified*

The shims under asmpython/stdlib/*.py are written in the compiler's restricted subset and their parameter lists were deliberately simplified, in several places with docstrings admitting it (`Pass exception types positionally`, `using key0=var0 pairs (simplified)`, `For higher-arity products, call iteratively`, `no reflection needed`). Sema checks each call against that declared signature, so the CPython spelling is refused at compile time even though the algorithm behind it is present and produces the exactly-correct answer. This is NOT a compiler expressiveness gap: I verified `**kwargs` in a def, keyword calls, and `__init__(self, **kw)` all compile and behave exactly like CPython, so the shims could accept these arguments today. Three sub-shapes: (a) constructor drops CPython's optional initial-data argument (`md5`/`sha256` declare `__init__(self)`, `suppress` declares `__init__(self)`); (b) a named parameter is simply missing (`UUID.__init__(self, hex_str)` has no `int=`; `product(a, b=[])` has no `repeat=`); (c) `**kwargs` omitted where CPython has it (`Counter.__init__(self, iterable=[])`, `SimpleNamespace.__init__(self)`, `Template.substitute(self, mapping=0, var0='', key0='')`). Caveat established by probe: for `suppress` and only `suppress`, widening the signature will not clear the case, because the no-arg control also fails at runtime (the raise is not suppressed despite `__exit__` returning 1) - a second, independent defect sits behind it. Corpus-wide this shape is 8 cases here plus randrange, all inside this partition, so the repair is cheap but bounded.

Evidence:

```text
Every control below uses the shim's OWN declared spelling and passes, several reproducing the case's exact expected output:
  hashlib.md5() + h.update(b'hello') + h.hexdigest()
    -> GOT: 5d41402abc4b2a76b9719d911017c592   (== lib_hashlib_md5.py's expect block)
  uuid.UUID('00000000-0000-0000-0000-000000000000'); str(u)
    -> GOT: 00000000-0000-0000-0000-000000000000 (== lib_uuid_int.py's expect block)
  Counter(['a','a','b']) -> GOT: 2 1
  product([0,1],[0,1])   -> GOT: 4
  Template('$name is here').substitute(0,'x','name') -> GOT: x is here
  SimpleNamespace() + ns._set('x',1) + ns._get('x')  -> GOT: 1
While the CPython spellings are refused:
  [E021] md5() takes 0 argument(s), got 1
  [E021] UUID() got an unexpected keyword argument 'int'
  [E021] Counter() got an unexpected keyword argument 'a'
  [E021] product() got an unexpected keyword argument 'repeat'
  [E021] substitute() got an unexpected keyword argument 'name'
  [E021] SimpleNamespace() got an unexpected keyword argument 'x'
  [E021] suppress() takes 0 argument(s), got 1
Proof that **kwargs is NOT the blocker:
  def f(
...
```

Minimal repro:

```python
import hashlib
print(hashlib.md5(b'hello').hexdigest())

observed (asmpython): compile refused, [E021] md5() takes 0 argument(s), got 1
CPython:              5d41402abc4b2a76b9719d911017c592

# the shim's own declared spelling already produces exactly that value:
import hashlib
h = hashlib.md5()
h.update(b'hello')
print(h.hexdigest())        # asmpython GOT: 5d41402abc4b2a76b9719d911017c592
```

Cases: `lib_collections_counter_subtract.py`, `lib_contextlib_suppress.py`, `lib_hashlib_md5.py`, `lib_hashlib_sha256.py`, `lib_itertools_product_repeat.py`, `lib_string_template.py`, `lib_types_simplenamespace.py`, `lib_uuid_int.py`

### 3. Attribute access lowers to `_abi_dict_get_default(recv, name, 0)` with no check that the receiver is a heap instance; the any-tag guard is emitted on the call's RESULT instead  (8)  — *verified*

`obj.attr` is lowered unconditionally to `_abi_dict_get_default(<obj's raw 64 bits>, "attr", 0)`, i.e. the receiver's machine word is passed straight into a pointer parameter and dereferenced as an instance dict. No kind test guards the receiver. The lowerer DOES emit the full `Lanytagnone/rawint/heap/gc_is_object/box/instcheck` discriminator chain at every one of these sites -- but it applies it to the value the helper RETURNED, never to the receiver it is about to dereference. The guard is on the wrong side of the call. When the receiver is a float (raw f64 bits), a small int, a NULL from a stubbed definition, or a str/frozenset heap object (a real pointer, but not an instance dict), the helper walks a non-dict and the process takes an access violation.

Evidence:

```text
complex_via_real.ir, func main -- an f64 SSA value is passed as arg0 of a ptr-parameter helper:
    %%t2: f64 = const 3.0
    %%t3: ptr = global_addr __str_1        ; '__class__'
    %%t4: i64 = const 0
    %%t5: ptr = call _abi_dict_get_default, %%t2, %%t3, %%t4
(0x4008000000000000 is then dereferenced.) The any-tag chain follows immediately, but discriminates %%t5 -- the result -- not %%t2.

docstring_module_access.ir, func main -- receiver is an alloca that is never stored to:
    %%t2: ptr = alloca
    %%t3: i64 = load %%t2
    %%t4: ptr = global_addr __str_1        ; '__doc__'
    %%t6: ptr = call _abi_dict_get_default, %%t3, %%t4, %%t5

Mechanical scan of all 291 pre-dumped corpus IRs for `_abi_dict_get_default/_abi_dict_set/_abi_dict_update` whose receiver temp is declared i64/f64 or is an unwritten alloca: 34 of 291 files match.

Note on measurement: stdout is block-buffered and DISCARDED on the AV. Probe zz5_a2.py begins with `print("BEFORE")` and still yields `STDOUT (0 bytes)`. So the empty `got` field on every RUN-CRASH triage record carries no information about where the
...
```

Minimal repro:

```python
Four one-line probes, each built with --no-pyinbin-fallback and run directly:

  x = (3.0).__class__   -> EXIT 3221225477 (0xc0000005)   CPython: <class 'float'>
  x = (5).__class__     -> EXIT 3221225477 (0xc0000005)   CPython: <class 'int'>
  x = "abc".__class__   -> EXIT 3221225477 (0xc0000005)   CPython: <class 'str'>

  class MyErr(Exception):
      def __init__(self, code):
          self.code = code
  try:
      raise MyErr(404)
  except MyErr as e:
      c = e.code            -> EXIT 3221225477 (0xc0000005)   CPython: 404

The attribute READ alone crashes -- no print, no further use. A `str` receiver crashes too, so this is not merely 'unboxed scalar used as pointer': any receiver that is not a user-class instance dict is walked as one.
```

Cases: `compat_class_registry.py`, `compat_metaclass_descriptor_collect.py`, `compat_type_parameter_specialize.py`, `complex_via_real.py`, `custom_exception_attr.py`, `docstring_module_access.py`, `exc_args_tuple.py`, `lib_array_typecodes.py`

### 4. An `any`-element container is read with TAGGED_REPR_KIND (6), which requires a BOX_MAGIC header, but the matching `_abi_list_append` write stores the value raw — the magic check fails and the raw pointer is printed in decimal  (7)  — *verified*

When a list's element type resolves to `"any"`, `_composite_repr_kind` (ir_lower.py:3187) answers TAGGED_REPR_KIND = 6, i.e. "every cell carries its own runtime tag". The runtime honours that literally: codegen.py:9057 `._fe_tagged` requires the cell to be above 0x10000, 8-byte aligned, and to have `0xB0BE11EDB0BE11ED` at offset 0; anything else falls through to `._fe_tagged_int` and is printed with `_emit_int_to_str`. But the write side — `call _abi_list_append, <list>, <value>` — emits no `_abi_new_box`, so the cell holds a raw `str`/`list` pointer. The read contract says tagged, the write contract says raw, and every heap or rodata pointer therefore renders as a 6-11 digit decimal. This is the concrete write/read asymmetry behind the corpus-wide `anyunbox` high / `_abi_new_box == 0` signature.

Evidence:

```text
Minimal repro's full IR (probe --ir):

    call _abi_list_append, %%t11, %%t13     ; %%t13: ptr = load p  -- stored RAW
    %%t16: i64 = const 6                    ; TAGGED_REPR_KIND
    %%t17: ptr = call _abi_list_repr, %%t15, %%t16

IR-SIGNALS for that build: _abi_new_box=0  anyunbox=0. Adding the annotation
`r: list[list]` changes nothing on the write side but makes the kind non-tagged,
and the same program prints `[[1, 2]]`.

Const 6 confirmed at the print site of every case below via the pre-dumped IR,
e.g.
  _ir/lib_itertools_repeat.ir:147-148   %%t10: i64 = const 6 / call _abi_list_repr
  _ir/lib_itertools_pairwise.ir:125-126
  _ir/lib_itertools_combinations.ir:430-431
  _ir/lib_itertools_permutations.ir:811-812
  _ir/lib_itertools_compress.ir:358-359
  _ir/sorted_multiple_criteria.ir:440-441
  _ir/r39_priority_sort.ir:285-286

Runtime side, codegen.py:9077-9083:
  "cmp rax, 0x10000", "jbe ._fe_tagged_int",
  "test rax, 7",      "jnz ._fe_tagged_int",
  "mov rbx, 0xB0BE11EDB0BE11ED", "cmp [rax], rbx", "jne ._fe_tagged_int"
```

Minimal repro:

```python
# minimal
r: list = []
p: list = [1, 2]
r.append(p)
print(r)
# asmpython: [7021504]
# CPython:   [[1, 2]]
# annotating `r: list[list]` prints [[1, 2]] correctly

# the itertools.repeat shape (shim param is `x: object`, accumulator is a bare `list`)
def rep(x: object, n: int) -> list:
    r: list = []
    i = 0
    while i < n:
        r.append(x)
        i = i + 1
    return r
print(rep('x', 3))
# asmpython: [5368741898, 5368741898, 5368741898]
# CPython:   ['x', 'x', 'x']

# the sorted+comprehension shape -- sorted_multiple_criteria / r39_priority_sort
people = [('alice', 30), ('bob', 25)]
s = sorted(people, key=lambda p: p[1])
print([p[0] for p in s])
# asmpython: [5368741928, 5368741922]
# CPython:   ['bob', 'alice']

# NOTE the contrast that bounds the cause: appending a *literal* refines the
# element type and works --
#   r = []
#   for i in range(2): r.append('x')
#   print(r)          -> ['x', 'x']   (correct)
```

Cases: `lib_itertools_combinations.py`, `lib_itertools_compress.py`, `lib_itertools_pairwise.py`, `lib_itertools_permutations.py`, `lib_itertools_repeat.py`, `r39_priority_sort.py`, `sorted_multiple_criteria.py`

### 5. shims that read as complete implement a materially different algorithm, so they return well-typed wrong data with no stub marker to find them by  (5)  — *verified*

Unlike the self-documented stubs, these shims carry ordinary docstrings and would pass a code review, but their algorithms are not CPython-equivalent. heapq.nlargest heapifies and then takes the TAIL of the heap array (`h[total-1-count]`), which is not the n largest in any order. itertools.cycle is `def cycle(iterable: list, n: int = 2) -> list` -- a finite 2x materialisation, not an unbounded iterator, so islice over it truncates. re's Match.group ignores its `n` argument entirely and always returns the whole match; no capture groups are tracked. Fraction.__init__ annotates `numerator: int`, so there is no float-to-rational path at all. json's loads family is documented to stringify every parsed value (`loads_list(s) -> list[str]`). The compiler is not involved in any of these -- I predicted each observed output from the shim source and matched it exactly.

Evidence:

```text
heapq -- shim asmpython/stdlib/heapq.py:101-115 does `heapify(h)` then `result.append(h[total - 1 - count])`. Simulated in CPython: heapify([1,5,2,8,3]) -> [1,3,2,8,5]; h[4],h[3],h[2] = [5, 8, 2]. Observed triage diff_got for lib_heapq_nlargest: '[5, 8, 2]' -- exact match. CPython wants [8, 5, 3].

itertools -- shim asmpython/stdlib/itertools.py:83-91: `def cycle(iterable: list, n: int = 2) -> list:` builds n=2 repetitions.
  Probe q8_cycle_only:   `print(cycle([1,2]))`  -> GOT '[1, 2, 1, 2]'  (CPython: <itertools.cycle object>)
  Probe q8_islice_plain: `print(list(islice([1,2,3,4,5,6], 5)))` -> '[1, 2, 3, 4, 5]' == CPython, so islice is fine.
  => islice(cycle([1,2]), 5) slices a 4-element list -> 4 elements. Matches diff_got '[1, 2, 1, 2]'.

re -- shim asmpython/stdlib/re.py:52-53:
  def group(self, n: int = 0) -> str:
      return self._string[self._start:self._end]     <- `n` is never read
  Probe q8_re: m = re.search(r'(\w+)@(\w+)', 'user@host'); group(0)/group(1)/group(2)
    GOT 'user@host' / 'user@host' / 'user@host'   CPY 'user@host' / 'user' / 'host'

fractions -- shim asmp
...
```

Minimal repro:

```python
import heapq
print(heapq.nlargest(3, [1, 5, 2, 8, 3]))
# asmpython: [5, 8, 2]   (tail of the heap array)
# CPython:   [8, 5, 3]

from itertools import cycle, islice
print(cycle([1, 2]))                      # asmpython [1, 2, 1, 2]  -- a finite list
print(list(islice(cycle([1, 2]), 5)))     # asmpython [1, 2, 1, 2]  CPython [1, 2, 1, 2, 1]

import re
m = re.search(r'(\w+)@(\w+)', 'user@host')
print(m.group(1), m.group(2))             # asmpython 'user@host user@host'  CPython 'user host'

from fractions import Fraction
print(Fraction(0.5))                      # asmpython '1'    CPython '1/2'
print(Fraction(1, 2))                     # asmpython '1/2'  == CPython

import json
print(json.loads('[1, 2, 3]'))            # asmpython ['1', '2', '3']  CPython [1, 2, 3]
```

Cases: `lib_fractions_from_float.py`, `lib_heapq_nlargest.py`, `lib_itertools_cycle.py`, `lib_json_roundtrip.py`, `lib_re_groups.py`

### 6. An unmodelled call is silently lowered to `const 0`, and the next operation uses that 0 as a live heap pointer  (5)  — *verified*

When the backend cannot model a callee (a stdlib function like `chain.from_iterable`/`tee`/`TopologicalSorter.static_order`, or a method on an `"any"`-typed receiver like `p.strip('/')`), it emits no call at all — it substitutes `const 0` as the result and drops the operands on the floor. Nothing in the pipeline marks that 0 as unusable, so the very next consumer treats it as a real object pointer: `_abi_list_slice(0, MIN, MAX)`, `_abi_list_append(l, 0)` then `_abi_str_join`, `_abi_dict_get_default(0, ...)`. The dereference of address 0 is the 0xC0000005. Merely *storing* the stub is harmless (S1 exits 0); the AV appears the moment the value is consumed as a heap object, so the crash site is always one step removed from the real defect. This is the CONTEXT.md 'graceful stub' shape, except it does not gracefully yield 0/None — it faults.

Evidence:

```text
lib_itertools_chain_from_iter.ir — the three sublists are built into %%t5 and then *discarded*; the chain call is gone:
    call _abi_list_append, %%t5, %%t14
    %%t17: i64 = const 0                              <- chain.from_iterable(...) became 0
    %%t18: i64 = const -9223372036854775808
    %%t19: i64 = const 9223372036854775807
    %%t20: ptr = call _abi_list_slice, %%t17, %%t18, %%t19    <- list(0): NULL deref

path_join_manual.ir, Lcompappend4 — `p.strip('/')` became 0, both operands computed then unused:
    %%t34: ptr = load %%t11
    %%t35: i64 = load %%t33            <- p          (dead)
    %%t36: ptr = global_addr __str_1   <- '/'        (dead)
    %%t37: i64 = const 0               <- p.strip('/') became 0
    call _abi_list_append, %%t34, %%t37
  ...
    %%t42: ptr = call _abi_str_join, %%t3, %%t41     <- joins a list of NULLs

lib_itertools_tee.ir — tuple-unpack of the stub gives both targets 0:
    %%t8: ptr = const 0
    %%t9: ptr = const 0

lib_graphlib_topo.ir L125/L128 (same block Lanyunboxend17):
    %%t69: i64 = const 0
    %%t72: ptr = call _abi_list_slice,
...
```

Minimal repro:

```python
from itertools import chain
x = chain.from_iterable([[1, 2]])
print(1)
# asmpython: exit 0, prints "1"   (storing the stub is harmless)

from itertools import chain
print(list(chain.from_iterable([[1, 2]])))
# asmpython: exit 0xC0000005      CPython: [1, 2]

def jp(*parts):
    return "/".join(p.strip("/") for p in parts)
print(jp("usr", "local"))
# asmpython: exit 0xC0000005      CPython: usr/local
```

Cases: `lib_graphlib_topo.py`, `lib_itertools_chain_from_iter.py`, `lib_itertools_tee.py`, `ospath_splitext.py`, `path_join_manual.py`

### 7. sema's `_collect_returns` never descends into `A.Match` case bodies, so a function whose only `return`s live inside a `match` is declared `-> i64`  (5)  — *verified*

`A.Match` (ast_nodes.py:729) is rewritten into an If/elif chain by `Analyzer._check_stmt`, but that rewrite runs AFTER return-type inference. At inference time the body still holds a raw `A.Match` node whose sub-blocks live in `cases: list[(Pattern, guard, body)]`. Neither `_collect_returns` walks it: sema.py:7063's isinstance dispatch has branches only for `A.If/While/For/Try`, and sema.py:3082's variant probes attributes named `body/then/orelse/handler/else_body/finally_body` — `Match` has only `subject`, `cases`, `pos`. So `_infer_return_type` (sema.py:3093) sees zero returns, returns None, and the function gets the default `i64` return type. The caller then formats the returned `str` pointer with `_abi_int_to_base`, printing a rodata address in decimal.

Evidence:

```text
_ir/match_literal.ir declares the function as int-returning while every ret is a pointer:

  func f(%%arg_x: i64) -> i64 {
    Lthen2:
      %%t8: ptr = global_addr __str_1
      ret %%t8
  ...
  main:
      %%t3: i64 = call f, %%t2
      %%t6: ptr = call _abi_int_to_base, %%t3, %%t4, %%t5

Probe series (all at head):
  match-only, no top-level return   -> 5368737792
  match + trailing top-level return -> one        (correct)
  `def f(x) -> str:` + match        -> one        (correct)
  if/else only, no top-level return -> one        (correct)
  return inside a for body          -> one        (correct)
So If/For/While bodies ARE walked; only `match` case bodies are not.
```

Minimal repro:

```python
def f(x):
    match x:
        case 1:
            return 'one'
        case _:
            return 'other'
print(f(1))

asmpython: 5368737792
CPython:   one

# Two one-line confirmations of the same mechanism:
#   adding `-> str` to the signature      -> prints 'one'
#   moving `return 'other'` out of the match to function top level -> prints 'one'
```

Cases: `match_class_pattern.py`, `match_guard.py`, `match_literal.py`, `match_or_pattern.py`, `match_sequence.py`

### 8. `str` is a UTF-8 byte string, not a sequence of code points  (5)  — *verified*

`len` counts bytes, `ord` returns the first UTF-8 byte, and indexing yields a single byte, so every operation on non-ASCII text is wrong.

Evidence:

```text
len('héllo') -> 6 (want 5);  ord('中') -> 228 (want 20013, 228 = 0xE4)
```

Minimal repro:

```python
s = 'héllo'
print(len(s))      # asmpython 6      CPython 5
print(ord('中'))   # asmpython 228    CPython 20013
```

Cases: `str_unicode_len.py`, `unicode_emoji_len.py`, `unicode_in_list_repr.py`, `unicode_ord_high.py`, `unicode_upper_accent.py`

### 9. Genuinely unimplemented stdlib callables, not lost receiver kinds: the math binding table is scalar-only and time has no struct_time record type  (5)  — *strong*

These five E120 refusals are honest gaps, and should not be filed with the type-lattice group -- the module object is resolved correctly and the name is simply absent from the binding table. Two of them are structural rather than one-line omissions. `asmpython/stdlib/math.py` declares every entry as `Func(arg_types=("float",...), ret_type="float", c_name=<libm symbol>)`, a shape that maps 1:1 onto a libm call and therefore cannot express `prod`/`dist`, which take sequences. `time.py`'s table is likewise scalar-only, so `gmtime`/`struct_time`/`strftime` need a `struct_time` record type to exist first. `random.gauss` is the only plain missing row.

Evidence:

```text
Registered names, enumerated from the binding tables:
  math.py:   pi e tau inf nan sqrt cbrt exp log log2 log10 sin cos tan asin acos atan sinh cosh
             tanh floor ceil trunc fabs asinh acosh atanh exp2 expm1 log1p nearbyint pow atan2
             hypot fmod copysign nextafter remainder fdim fmax fmin isnan isinf isfinite degrees
             radians gcd lcm factorial comb perm log_base modf_frac modf_int frexp_mantissa
             frexp_exponent ldexp isqrt isclose erf erfc gamma lgamma
             -- no `prod`, no `dist`
  random.py: seed rand random randint uniform randrange choice shuffle sample getrandbits
             -- no `gauss`
  time.py:   time sleep sleep_ms clock difftime perf_counter monotonic time_ns
             -- no `gmtime`, no `struct_time`, no `strftime`
Table shape (math.py:20): `"sqrt": Func(arg_types=("float",), ret_type="float", c_name="sqrt")`
  -- scalar arg_types only; there is no way to declare a sequence parameter.
E120 is raised at sema.py:11109: `f"module {e.obj.name!r} has no callable {e.method!r}"`
```

Cases: `lib_math_dist.py`, `lib_math_prod.py`, `lib_random_gauss.py`, `lib_time_strftime.py`, `lib_time_struct.py`

### 10. Frontend productions are fixed-shape: clause lists, nested lambdas, yield-expressions and f-string specs are hard parse errors  (4)  — *verified*

The hand-written lexer/parser implements each construct at a fixed arity and a fixed syntactic slot rather than as a repeating or recursive production, so any legal repetition or nesting is a hard SyntaxError before sema ever runs. Probing separates the shape from the feature: a list comprehension accepts multiple `for` clauses but only ONE `if`; a dict comprehension accepts only ONE `for`. `lambda` is not an expression atom (it cannot be a lambda body); `yield` is a statement-only production and is rejected even parenthesized with a value; `;` is not in the tokenizer's character set at all ([L002], the lexer layer); and the f-string sub-scanner has no case for the `=` debug spec. These are five distinct missing productions in one component, not one bug -- but they share the shape, and the backend semantics already exist for the first four (statement sequencing, one-if comprehensions, single lambdas and f-string formatting all compile and run today). `generator_send_skip` is the exception: parsing `yield` as an expression is necessary but not sufficient, since `.send()` also needs generator resumption-with-a-value at runtime.

Evidence:

```text
$ probe --code "a = 1; b = 2"
  SyntaxError: [L002] unexpected character ';'

$ probe --code "m = {i*10+j: i*j for i in range(3) for j in range(3)}"
  SyntaxError: [P002] expected OP '}', got KEYWORD 'for'
$ probe --code "v = [i*j for i in range(3) for j in range(3)]"      <- CONTROL
  COMPILE: ok / RUN: exit 0 / GOT: 4

$ probe --code "v = [i for i in range(6) if i > 1 if i < 4]"
  SyntaxError: [P002] expected OP ']', got KEYWORD 'if'
$ probe --code "v = [i for i in range(6) if i > 1]"                 <- CONTROL
  COMPILE: ok / GOT: [2, 3, 4, 5]

$ probe --code "add = lambda x: lambda y: x + y"
  SyntaxError: [P001] unexpected token KEYWORD 'lambda'

$ probe --code "def g():\n    x = (yield 5)"     (parenthesized, with a value)
  SyntaxError: [P001] unexpected token KEYWORD 'yield'

$ probe --code "x = 42\nprint(f'{x=}')"
  parse error: unexpected tokens in f-string expression: 'x='

Corpus-wide sizing from triage.jsonl: 17 of the 98 COMPILE-FAILs are frontend
SyntaxErrors. The 11 outside this partition (all in A_unmerged_branch) show the
same fixed-shape signature and cluster into
...
```

Minimal repro:

```python
# 1. semicolon -- lexer does not know the character
a = 1; b = 2
print(a + b)
# asmpython: SyntaxError: [L002] unexpected character ';'   CPython: 3

# 2. dict comprehension with two for-clauses (list comp with two fors is FINE)
m = {i*10+j: i*j for i in range(3) for j in range(3)}
# asmpython: [P002] expected OP '}', got KEYWORD 'for'      CPython: works

# 3. comprehension with two if-clauses (one if is FINE)
v = [i for i in range(6) if i > 1 if i < 4]
# asmpython: [P002] expected OP ']', got KEYWORD 'if'       CPython: [2, 3]

# 4. lambda is not an expression atom
add = lambda x: lambda y: x + y
# asmpython: [P001] unexpected token KEYWORD 'lambda'       CPython: works

# 5. yield in expression position, even parenthesized with a value
def g():
    x = (yield 5)
# asmpython: [P001] unexpected token KEYWORD 'yield'        CPython: works

# 6. f-string = debug spec
x = 42
print(f'{x=}')
# asmpython: parse error: unexpected tokens in f-string expression: 'x='
# CPython: x=42
```

Cases: `dict_dict_comprehension.py`, `fstring_equals_debug.py`, `generator_send_skip.py`, `lambda_nested.py`

### 11. An attribute-lookup miss returns the dict-default sentinel 0, which is then dereferenced as an object or called as a function pointer  (4)  — *verified*

Class objects are created as bare `_abi_new_instance` and their namespaces are never materialised — no method or class-level binding is ever stored into `@__classobj_*`. Every attribute read lowers to `_abi_dict_get_default(recv, name, 0)`, so a miss is indistinguishable from a hit that returned 0. The sentinel then flows straight into a use: as a dict receiver for the next link of an attribute chain (`Color.RED.value` -> `dict_get_default(0, 'value', 0)`), or, for a method taken as a value, as an indirect callee (`%%t17: ptr = call %%t19` with %%t19 == 0). Both fault. Statically-known class attributes are resolved by a different path and work (C1 prints 5), which bounds this to reads the compiler could not resolve — exactly the dynamic cases: metaclass-populated namespaces, Enum members, and bound methods. Note CPython raises AttributeError for a genuine miss; asmpython faults instead, so this is also a safety defect, not only a conformance one.

Evidence:

```text
metaclass_basic.ir — @__classobj_C is created and stored, but the read of C.created uses `const 0` as the receiver instead of loading it:
    %%t2: ptr = call _abi_new_instance
    %%t3: ptr = global_addr __classobj_C
    store %%t2, %%t3
    ...
    %%t6: i64 = const 0                      <- receiver for C.created is 0, not __classobj_C
    %%t7: ptr = global_addr __str_1          <- 'created'
    %%t8: i64 = const 0                      <- default
    %%t9: ptr = call _abi_dict_get_default, %%t6, %%t7, %%t8

returning_bound_method.ir — g.greet misses the instance dict, yields the default 0, and is then called:
    %%t15: ptr = call _abi_dict_get_default, %%t12, %%t13, %%t14   ; 'greet', default 0
    %%t16: ptr = global_addr method
    store %%t15, %%t16
    %%t18: ptr = global_addr method
    %%t19: ptr = load %%t18
    %%t17: ptr = call %%t19                                        ; call through NULL

lib_enum_basic.ir declares 11 empty class objects (@__classobj_Color, @__classobj_Enum, ...) all built by `_abi_new_instance` with no member installation.
```

Minimal repro:

```python
class G:
    def g(self): return 1
x = G()
print(x.g())      # asmpython: exit 0, prints 1   (direct call is fine)
m = x.g
print(m())        # asmpython: exit 0xC0000005    CPython: 1

class C:
    x = 5
print(C.x)        # asmpython: exit 0, prints 5   (static attr resolves)

class C:
    pass
print(C.created)  # asmpython: exit 0xC0000005    CPython: AttributeError

from enum import Enum
class Color(Enum):
    RED = 1
print(Color.RED.value)   # asmpython: exit 0xC0000005   CPython: 1
```

Cases: `lib_enum_auto.py`, `lib_enum_basic.py`, `metaclass_basic.py`, `returning_bound_method.py`

### 12. The assignment/deletion target grammar is a strict subset of the rvalue grammar: strided slice stores and attribute/slice del targets are unimplemented  (4)  — *verified*

Slices and `del` targets are lowered by narrower dispatchers than the corresponding rvalue paths. Reading `a[1:3]` works and storing through a unit-stride slice `a[1:3] = [...]` works, but adding a step refuses with `unsupported stmt IndexAssign (slice step)` -- the store path models only stride 1. `del` is implemented for Name, dict-key Subscript and integer-index Subscript targets, all verified working; a slice target falls through to the EXPRESSION lowerer (note the error says `unsupported expr Slice`, not `stmt`, i.e. the del path evaluates its target as an rvalue and that lowerer has no Slice case in target position), and an attribute target is explicitly refused. Crucially `del obj.attr` fails for a PLAIN attribute with no property involved, so `property_deleter` is not a descriptor/`@x.deleter` problem at all -- the `@x.deleter` machinery is never reached.

Evidence:

```text
$ probe --code "a=[1,2,3,4,5]\nprint(a[1:3])"        COMPILE: ok  GOT: [2, 3]
$ probe --code "a=[1,2,3,4,5]\na[1:3]=[9]"           COMPILE: ok  GOT: [1, 9, 3, 4, 5]
$ probe --code "a=[1,2,3,4,5]\na[::2]=[7,7,7]"
  asmpython: unsupported stmt IndexAssign (slice step)
$ probe --code "a=[1,2,3,4,5]\ndel a[1:3]"
  asmpython: unsupported expr Slice          <- 'expr', not 'stmt'

del target coverage, all four probed at head:
  del x            (Name)              COMPILE: ok   GOT: ok
  del d['a']       (dict Subscript)    COMPILE: ok   GOT: ['b']
  del a[1]         (index Subscript)   COMPILE: ok   GOT: [1, 3]
  del c.v          (plain Attribute)   asmpython: unsupported stmt Del (Attr)

$ probe --code "class C:\n    def __init__(self):\n        self.v=1\nc=C()\ndel c.v"
  asmpython: unsupported stmt Del (Attr)
     ^ no @property anywhere -- del-on-attribute is unimplemented outright
```

Minimal repro:

```python
a = [1, 2, 3, 4, 5]
print(a[1:3])        # asmpython: [2, 3]              (rvalue slice: ok)
a[1:3] = [9]         # asmpython: [1, 9, 3, 4, 5]     (unit-stride store: ok)

a[::2] = [7, 7, 7]   # asmpython: unsupported stmt IndexAssign (slice step)
                     # CPython:   [7, 9, 7, 4, 7]
del a[1:3]           # asmpython: unsupported expr Slice
                     # CPython:   [1, 4, 5]

class C:
    def __init__(self):
        self.v = 1
c = C()
del c.v              # asmpython: unsupported stmt Del (Attr)   -- NO property involved
                     # CPython:   works
```

Cases: `del_slice.py`, `extended_slice_assign.py`, `property_deleter.py`, `slice_assignment_step.py`

### 13. 3-arg type(name, bases, ns) is unimplemented, so the namedtuple shim cannot construct its class and every namedtuple call is refused  (4)  — *verified*

asmpython/stdlib/collections.py is exactly 756 lines and its last line, 756:12, is `return type(typename, (_NamedTupleBase,), namespace)`. The compiler's `type()` binding models only the 1-arg introspection form; the 3-arg class-construction form is absent because classes are built statically at compile time. Sema refuses the shim's own line with E021, but reports it against the *user's* file path, so all four cases show `<case>.py:756:12` even though the case files are 6-12 lines long. The refusal is a property of `type()` itself, not of collections: a standalone 3-arg `type()` with no stdlib involved is refused identically. Importing `namedtuple` without calling it compiles fine, so the shim body is lowered on demand, not eagerly.

Evidence:

```text
Probe on a standalone snippet (no stdlib):
  COMPILE: FAILED  codes=['[E021]', '[E001]']
  tmp427i2eyb.py:4:5: TypeError: [E021] type() takes 1 argument(s), got 3
    T = type('T', (B,), {})
        ^
All four corpus cases report the identical shim position, e.g.
  lib_collections_namedtuple.py:756:12: TypeError: [E021] type() takes 1 argument(s), got 3
and collections.py:756 is literally `    return type(typename, (_NamedTupleBase,), namespace)`.
Control (import without call) compiles and runs: `from collections import namedtuple; print('imported')` -> COMPILE: ok / GOT: imported.
```

Minimal repro:

```python
class B:
    pass
T = type('T', (B,), {})
print(T)

observed (asmpython): compile refused, [E021] type() takes 1 argument(s), got 3
                      plus a cascading [E001] undefined variable 'T'
CPython:              <class '__main__.T'>
```

Cases: `296_collections_namedtuple.py`, `lib_collections_namedtuple.py`, `lib_collections_namedtuple_methods.py`, `namedtuple_unpacking.py`

### 14. When `_infer_return_type` cannot type a function's returns it falls back to `i64` rather than to `any`, so every returned pointer/float/tuple-element is formatted as a decimal at the call site  (4)  — *verified*

`_infer_return_type` (sema.py:3093) only succeeds when every reachable return's value is statically knowable via `_literal_arg_type` and all agree. A tuple return, a `return seq[i]`, or a `return v` of an untyped parameter all yield None, and the function is then emitted as `-> i64`. The failure is not the inference gap itself but the fallback choice: `i64` is a *claim* about the value (format it as an integer) rather than an admission of ignorance (`any`, which would box and dispatch). The same defect appears in the return *annotation* path: a bare `-> list` carries no element type, so a subscript of the result defaults to `int` too (`_list_repr_kind`'s `getattr(e,'list_el_type','int') or 'int'`, ir_lower.py:3203).

Evidence:

```text
IR headers show the i64 claim against pointer/float bodies:
  _ir/vm_str_field_via_helper.ir:16   func newest(%%arg_seq: ptr) -> i64
  _ir/vm_tuple_through_any.ir:11      func passthrough(%%arg_v: i64) -> i64
and the print sites are `_abi_int_to_base` (_ir/vm_str_field_via_helper.ir:716, _ir/vm_tuple_through_any.ir:2198-2200, _ir/r40_mean_variance.ir:336-345).

Probes:
  return a single float  -> 5.0                              (correct; scalar inference works)
  return a 2-tuple of floats, unpack -> 4617315517961601024 4617315517961601024
     (0x4014000000000000 == IEEE bits of 5.0)
  helper returning seq[len(seq)-1], then read a str field -> 5368754216
  `def g(u: str) -> list: return [u,'']` then `g('x')[0]`  -> 5368745985
```

Minimal repro:

```python
# (a) tuple return -- r40_mean_variance
def f(d):
    m = sum(d) / len(d)
    return m, m
a, b = f([2, 4, 6, 8])
print(a, b)
# asmpython: 4617315517961601024 4617315517961601024   (raw IEEE bits of 5.0)
# CPython:   5.0 5.0

# (b) instance out of a list through an unannotated helper -- vm_str_field_via_helper
class L:
    def __init__(self, tag):
        self.tag = tag
def newest(seq):
    return seq[len(seq) - 1]
x = newest([L('a'), L('b')])
print(x.tag)
# asmpython: 5368754216
# CPython:   b

# (c) bare `-> list` annotation loses the element type -- lib_mimetypes
def g(u: str) -> list:
    return [u, '']
print(g('x')[0])
# asmpython: 5368745985
# CPython:   x
```

Cases: `lib_mimetypes.py`, `r40_mean_variance.py`, `vm_str_field_via_helper.py`, `vm_tuple_through_any.py`

### 15. A refused statement leaves its target unbound, so every later use emits a spurious cascade E001 that inflates the 'undefined name' bucket  (4)  — *verified*

When sema refuses a statement (E055, E023, E002, E005) it records no binding for the assignment target and keeps going, so the next use of that name emits E001 'undefined variable'. The cascade E001 is indistinguishable from a real one in the `codes` field, which is why four cases whose actual blocker is a self-describing feature refusal were partitioned as undefined-name failures. For class_hash_in_set.py the real and only blocker is E055 -- sets are str/int-keyed in v1 and `{K(1), K(1), K(2)}` needs user `__hash__`/`__eq__` dispatch in the set runtime, a stated v1 limitation. Triage should attribute a COMPILE-FAIL to its FIRST diagnostic and drop codes emitted at positions after an already-refused binding.

Evidence:

```text
class_hash_in_set.py (real cause is E055; E001 is cascade on `s`):
  :10:6: TypeError: [E055] set elements of type instance:K are not supported yet (sets are str/int-keyed in v1)
    s = {K(1), K(1), K(2)}
         ^
  :11:11: NameError: [E001] undefined variable 's'
    print(len(s))
              ^

zip_star_unpack.py (real cause is E023; E001 is cascade on `nums`):
  :4:18: semantic error: [E023] *expr argument unpacking requires a tuple with known element types
    nums, lets = zip(*pairs)
                     ^
  :5:7: NameError: [E001] undefined variable 'nums'

Same shape on zx_m1 (E005 then E001 'w') and complex_number_basic.py (E002 then E001 'z').
```

Minimal repro:

```python
class K:
    def __init__(self, v):
        self.v = v
    def __hash__(self):
        return self.v
    def __eq__(self, o):
        return self.v == o.v
s = {K(1), K(1), K(2)}
print(len(s))

CPython: 2
asmpython (head): TWO diagnostics --
  [E055] set elements of type instance:K are not supported yet   <- the real blocker
  [E001] undefined variable 's'                                  <- cascade only
Only the first is a cause; the second is why this case was bucketed as an undefined-name failure.
```

Cases: `class_hash_in_set.py`, `complex_number_basic.py`, `lib_csv_writer.py`, `zip_star_unpack.py`

### 16. `bytes` and `bytearray` are the static type `list`, so they render as a list of ints  (4)  — *verified*

They are not absent, contrary to the previous audit: `len`, indexing and mutation all work. sema maps them to `"list"`, so only the repr differs — and the compiler says so itself when a bytes method is missing. PHASE1.md scopes the real fix at 145 `== "list"` comparison sites, i.e. larger than the UNKNOWN_TY split, and rules out tagging the AST node as another side channel.

Evidence:

```text
bytes_decode.py refuses with: unsupported expr MethodCall (list.decode)
int_to_bytes -> [4, 0]          (CPython b'\x04\x00')
bytearray_mutate -> [120, 98, 99] (CPython bytearray(b'xbc'))
```

Minimal repro:

```python
print(b'abc')
# asmpython [97, 98, 99]   CPython b'abc'
```

Cases: `bytearray_mutate.py`, `int_to_bytes.py`, `vm_bytearray_mutable.py`, `vm_bytes_literal.py`

### 17. Nested / dynamic format specs are emitted literally instead of being evaluated  (4)  — *verified*

A format spec containing a replacement field (`{v:{w}.2f}`, `'{:.{}f}'.format`) is treated as literal text, so the spec source appears in the output.

Evidence:

```text
f'{3.14159:{w}.2f}' -> {w}.2f ;  '{:.{}f}'.format(3.14159, 2) -> 3.14159f}
```

Minimal repro:

```python
w = 6
print(f'{3.14159:{w}.2f}')
# asmpython {w}.2f   CPython '  3.14'
```

Cases: `fstring_nested_fstring.py`, `fstring_nested_spec.py`, `lib_string_formatter.py`, `str_format_nested_field.py`

### 18. Stdlib objects with no distinct static type are represented by a surrogate primitive, so every method that exists only on the real type is refused  (3)  — *verified*

When asmpython has no modeled type for a stdlib object it represents the object as a nearby primitive rather than as an opaque handle, and the method lookup then runs against the surrogate. `re.compile(p)` is an outright identity stub -- it returns the pattern string itself -- so a compiled pattern IS a `str` and `pat.findall(...)` is looked up as `str.findall`. `contextlib.closing` is a real modeled type when bound directly (E113 names it correctly), but its `__enter__` result is not, so `with closing(R()) as r` yields a `str`-typed binding and the synthesized exit-path `.close()` call is refused as `str.close` -- independently of the body. `bytes` is modeled as `list[int]` (already established in PHASE1.md), so `b'abc'.decode(...)` is looked up as `list.decode`. The diagnostic shape is identical in all three -- `unsupported expr MethodCall (<surrogate>.<method>)` -- and the giveaway is that the surrogate type name, not the method name, is the wrong half: substituting a nonsense method name yields the same surrogate.

Evidence:

```text
$ probe --code "import re\nprint(re.compile('x'))"
  COMPILE: ok / RUN: exit 0 / GOT: x          <- re.compile is IDENTITY

$ probe --code "import re\np = re.compile('x')\np.nosuchmethodatall()"
  asmpython: unsupported expr MethodCall (str.nosuchmethodatall)
     ^ surrogate is 'str' regardless of the method name

$ probe --code "import re\nm = re.match('x','x')\nm.nosuchmethodatall()"   <- CONTRAST
  [E113] ReMatch has no method 'nosuchmethodatall'
     ^ re.match DOES have a real modeled type; re.compile does not

$ probe --code "c = closing(R())\nc.nosuchmethodatall()"
  [E113] closing has no method 'nosuchmethodatall'   <- direct binding is tracked
$ probe --code "with closing(R()) as r:\n    pass"    (empty body!)
  asmpython: unsupported expr MethodCall (str.close)
     ^ the as-target is str; failure is on the synthesized exit call, not the body

$ probe --code "class CM:\n  __enter__/__exit__/hello ...\nwith CM() as c:\n    c.hello()"
  COMPILE: ok / GOT: hi      <- with/as is NOT broken generally; only this model is

Corpus-wide, 12 cases across partitions trace to surrogat
...
```

Minimal repro:

```python
import re
print(re.compile('x'))
# asmpython: prints  x        <- the "compiled pattern" is the pattern string
# CPython:   prints  re.compile('x')

p = re.compile(r'\d+')
print(p.findall('a1b22c'))
# asmpython: unsupported expr MethodCall (str.findall)
# CPython:   ['1', '22']

from contextlib import closing
class R:
    def close(self):
        print('closed')
with closing(R()) as r:
    pass
# asmpython: unsupported expr MethodCall (str.close)   (even with an empty body)
# CPython:   closed

# NOTE bytes_decode is attributed on the error message + the bytes-as-list[int]
# modeling already established in PHASE1.md; I did not re-probe it, per CONTEXT.
```

Cases: `bytes_decode.py`, `lib_contextlib_closing.py`, `lib_re_compile.py`

### 19. `list_el_type` is a side-channel AST attribute; a module-global binding never carries it, so globals silently default to `int` element kind  (3)  — *verified*

A container's element kind is not part of the type lattice. It is an attribute smeared onto AST expression nodes (`list_el_type`, `list_el_value_type`, `value_type`, `el_type`) and read back at ~40 sites with the pattern `getattr(e, "list_el_type", "int") or "int"` (ir_lower.py:3203, 3307, 4036, 5274, 6060, 7205; codegen.py:3348, 16684-16691). The module-global table is built by `list_el_ty.setdefault(s.target, getattr(s.value, "list_el_type", "int"))` (ir_lower.py:3868/3881/3885), so a global bound to a *Call* result — a node that carries no such attribute — is recorded as `int`-element, and later `append`/`__setitem__` writes (notably from inside a function body) never update the entry. The stored payload pointers are then handed to `_abi_list_repr` with element-kind tag `0` and formatted as decimal integers. This is the same disease PHASE1.md already documents for `is_none`/`is_bool`: a side channel that does not survive being stored.

Evidence:

```text
IR, _ir/app_matrix_rotate.ir (verbatim):
    %%t12: ptr = call rotate90, %%t3
    %%t13: ptr = global_addr result
    store %%t12, %%t13
    %%t14: ptr = global_addr result
    %%t15: ptr = load %%t14
    %%t16: i64 = const 0
    %%t17: ptr = call _abi_list_repr, %%t15, %%t16

_ir/function_with_side_effect_list.ir (verbatim):
    %%t3: ptr = call _abi_new_list, %%t2
    %%t4: ptr = global_addr log
    store %%t3, %%t4
    ...
    %%t11: i64 = const 0
    %%t12: ptr = call _abi_list_repr, %%t10, %%t11

The kind tag is `0` (= int) in both. Archive-wide tally of the 2nd arg to _abi_list_repr: kind=0 n=32, kind=1(str) n=10, kind=3(list) n=6, kind=6 n=127 — so tag 0 is a real, distinguishable wrong value, not the only value emitted.

Probe pair (same expression, only the binding differs):
  j4  print(rot([[1, 2], [3, 4]]))            -> [[3, 1], [4, 2]]      CORRECT
  k6  result = rot([[1, 2], [3, 4]]); print(result) -> [8398128, 8398272]  WRONG

Probe pair (module-scope write vs write inside a function; value is a *literal* in both):
  b2  g = []; g.append('a'); print(g)
...
```

Minimal repro:

```python
# 1. Element kind survives when the call result is printed directly,
#    and is lost when it is bound to a module global first.
def rot(m):
    n = len(m)
    return [[m[n - 1 - j][i] for j in range(n)] for i in range(n)]

print(rot([[1, 2], [3, 4]]))       # asmpython: [[3, 1], [4, 2]]   (correct)
result = rot([[1, 2], [3, 4]])
print(result)                       # asmpython: [8398128, 8398272]
                                    # CPython:   [[3, 1], [4, 2]]

# 2. A global container written from inside a function keeps the default
#    int element kind even though the written value is a str *literal*.
g = []
def r():
    g.append('a')
r()
print(g)                            # asmpython: [5368737792]
                                    # CPython:   ['a']
```

Cases: `app_matrix_rotate.py`, `function_with_side_effect_list.py`, `lib_copy_deepcopy.py`

### 20. marshal / reprlib / unicodedata ship no bundled source at all; the new E005 refusal replaced an older silent miscompile, so these cases' recorded symptoms are stale  (3)  — *verified*

Unlike csv/hmac/hashlib, these three have no `asmpython/stdlib/*.py` whatsoever, so E005 is literally correct: nothing to merge. `asmpython/pyinbin/native.py` carries host-Python bootstrap shims for `unicodedata` (line 888) and `marshal` (line 1199), which is why they appeared to work under the interpreter fallback; there is no native implementation and none for `reprlib` at all. This is a genuine feature gap, not a binding bug. Note the direction of travel: the case files record the OLD behaviour ('MISMATCH: prints 0', 'runtime failure exit 0xc0000005') from when an import resolving to nothing compiled to nothing and silently yielded 0. The current hard E005 -- whose text says 'An import that resolves to nothing compiles to nothing, so this call would silently do nothing at run time' -- is a strict improvement, and these three should be re-classified from OUTPUT-DIFF/CRASH to unimplemented-module.

Evidence:

```text
Bundled stdlib presence check (asmpython/stdlib/):
  csv: PRESENT (217 lines)      hmac: PRESENT (105 lines)
  hashlib: PRESENT (483 lines)  importlib: PRESENT (226 lines)  io: PRESENT (417 lines)
  marshal: ABSENT   reprlib: ABSENT   unicodedata: ABSENT

Interpreter-only shims:
  asmpython/pyinbin/native.py:888   if name == "unicodedata":
  asmpython/pyinbin/native.py:1199  if name == "marshal":
  (no reprlib anywhere)

Triage at head:
  lib_marshal_roundtrip.py:4:12 [E005] cannot call marshal....dumps(): no module 'marshal' is available
  lib_reprlib_repr.py:4:14     [E005] cannot call reprlib....repr(): no module 'reprlib' is available
  lib_unicodedata_name.py:4:18 [E005] cannot call unicodedata....category(): no module 'unicodedata' is available

Stale annotations inside the case files (describe a previous compiler):
  lib_marshal_roundtrip.py: '# asmpython (beta/3.14.0) MISMATCH: prints 0\n (wrong).'
  lib_unicodedata_name.py:  '# asmpython (beta/3.14.0) MISMATCH: prints 0\n (wrong).'
  lib_reprlib_repr.py:      '# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005'
```

Minimal repro:

```python
import reprlib
print(reprlib.repr(list(range(100)))[:20])

CPython: [0, 1, 2, 3, 4, 5, .
asmpython (head): [E005] cannot call reprlib....repr(): no module 'reprlib' is available

asmpython/stdlib/reprlib.py does not exist; nor marshal.py nor unicodedata.py.
The corpus records these as wrong-output/crash cases, which is no longer what happens.
```

Cases: `lib_marshal_roundtrip.py`, `lib_reprlib_repr.py`, `lib_unicodedata_name.py`

### 21. Features implemented only as compile-time expansion have no runtime fallback, so a non-literal or non-inlinable operand is refused outright  (3)  — *verified*

`str.format` and `map` are not runtime routines; they are compile-time transformations that require their operand to be visible as a constant or inlinable body at the call site. `'{} {}'.format(...)` on a LITERAL receiver compiles and runs, but binding the identical template to a variable first refuses with `unsupported expr MethodCall (str.format)` -- there is no runtime formatter to fall back to once the template is not a constant. `format_map` has no implementation on either path (it is refused even on a literal). `map` inlines a lambda and also accepts a named user function, but a bound method reference such as `str.upper` cannot be inlined and is refused -- so the diagnostic text "map() with a non-lambda predicate" misdescribes the boundary. The same gap has a silent-wrong-code sibling: assigning a builtin-type method reference to a variable and calling it COMPILES and then segfaults.

Evidence:

```text
$ probe --code "print('{} {}'.format('a', 1))"          <- LITERAL receiver
  COMPILE: ok / RUN: exit 0 / GOT: a 1
$ probe --code "t = '{} {}'\nprint(t.format('a', 1))"    <- SAME template, variable
  asmpython: unsupported expr MethodCall (str.format)

$ probe --code "print('{a}'.format_map({'a': 1}))"       <- literal, still refused
  asmpython: unsupported expr MethodCall (str.format_map)

$ probe --code "print(list(map(lambda x: x*2, [1,2])))"        COMPILE: ok  GOT: [2, 4]
$ probe --code "def f(x):\n    return x*2\nprint(list(map(f,[1,2])))"
                                                              COMPILE: ok  GOT: [2, 4]
$ probe --code "print(list(map(str.upper, ['a','b'])))"
  asmpython: unsupported expr Call (map() with a non-lambda predicate)
     ^ lambdas AND named functions both work; only the method reference fails

$ probe --code "f = str.upper\nprint(f('a'))"          <- LATENT WRONG-CODE
  COMPILE: ok
  RUN: exited 3221225477 (0xc0000005)   <- access violation, no diagnostic

Lead, not a claim: that last probe is an unguarded access violation from a
construct th
...
```

Minimal repro:

```python
# str.format works ONLY against a compile-time-visible literal template
print('{} {}'.format('a', 1))          # asmpython: a 1        (ok)
t = '{} {}'
print(t.format('a', 1))                # asmpython: unsupported expr MethodCall (str.format)
                                       # CPython:   a 1

print('{a}'.format_map({'a': 1}))      # asmpython: unsupported expr MethodCall (str.format_map)
                                       # CPython:   1

print(list(map(str.upper, ['a', 'b'])))
# asmpython: unsupported expr Call (map() with a non-lambda predicate)
# CPython:   ['A', 'B']

f = str.upper
print(f('a'))
# asmpython: COMPILES, then exits 0xc0000005 (access violation)
# CPython:   A
```

Cases: `map_method_ref.py`, `str_format_map.py`, `str_template_manual.py`

### 22. The repr-kind word encodes only two levels of container nesting: `_value_repr_kind` returns 0 (int) for `list`/`dict`/`tuple`, so anything nested three deep renders its innermost cells as decimal pointers  (3)  — *verified*

`_composite_repr_kind` (ir_lower.py:3187) packs a container as `3|4 | (_value_repr_kind(inner) << 4)`. `_value_repr_kind` (ir_lower.py:3177) only knows `str`->1, `float`->2, `any`->6, and falls through to `return 0` for every composite type. So `dict[str, dict[str, dict]]` encodes as `4 | (0<<4) == 4` — "dict with int values" — and the third-level dict pointer is formatted by `_emit_int_to_str`. The same 4-bit inner slot also swallows an unknown inner type: `_list_repr_kind`'s `getattr(e,'list_el_value_type','int') or 'int'` defaults to `int`, which is why `list[list]` whose inner elements are heterogeneous encodes as bare `3` and prints the inner strs as decimals. Container *literals* are exempt (ir_lower.py:3321 takes a per-element path), which is why depth-3 nesting only fails once a level stops being a literal in the caller's view.

Evidence:

```text
_ir/repr_nested_dict.ir:20-22
    %%t9:  i64 = const 1      ; key kind = str
    %%t10: i64 = const 4      ; value kind = dict, INNER kind = 0 (int)
    %%t11: ptr = call _abi_dict_repr, %%t2, %%t9, %%t10

_ir/lib_itertools_product.ir:188-189 and _ir/lib_itertools_zip_longest.ir:185-186
    %%t14: i64 = const 3      ; list of list, INNER kind = 0 (int)
    call _abi_list_repr, ...
which is exactly what the observed output shows -- the outer and middle levels
render, the innermost cell does not:
    lib_itertools_product     GOT [[1, 5368742071], [1, 5368742073], ...]
    lib_itertools_zip_longest GOT [[1, 5368741963], [2, 21505040], [3, 21505040]]
    repr_nested_dict          GOT {'a': {'b': 9250224}}

Corpus-wide, 5 pointerish cases carry a composite kind 3/4 at the print site:
deeply_nested_comprehension.py, lib_copy_deepcopy.py, lib_itertools_product.py,
lib_itertools_zip_longest.py, repr_nested_dict.py.
```

Minimal repro:

```python
print({'a': {'b': 1}})
# asmpython: {'a': {'b': 1}}          (correct -- two levels fit)
print({'a': {'b': {'c': 1}}})
# asmpython: {'a': {'b': 21964208}}
# CPython:   {'a': {'b': {'c': 1}}}
```

Cases: `lib_itertools_product.py`, `lib_itertools_zip_longest.py`, `repr_nested_dict.py`

### 23. A `lambda` body is name-resolved against globals + its own params only; the enclosing function's scope is not on its lookup chain  (3)  — *verified*

Sema builds the scope chain for a nested `def` correctly but not for a `lambda`. A lambda's body resolves names against the module globals and the lambda's own parameters; the enclosing FunctionDef's parameters and locals are simply not visible. The failure is at name resolution, before lowering (no IR is ever emitted), so it does not matter whether the lambda escapes: a lambda called in place inside the same function fails identically. When the missing name appears in call position sema reports it as E002 'undefined function' instead of E001 'undefined variable' — same lookup failure, different diagnostic. The nested-`def` closure path is fully wired (zx_c7 compiles and runs, emitting 1118 IR lines of `_abi_` closure machinery vs 39 for a trivial global read), so this is a gap in `lambda` specifically, not a missing closure feature.

Evidence:

```text
zx_c5 `x = 10; f = lambda: x + 1; print(f())`  -> COMPILE: ok / GOT 11 / VERDICT: PASS   (lambda over a GLOBAL works)

zx_c6 `def f(x):\n    g = lambda: x + 1\n    return g()`  ->
  COMPILE: FAILED  codes=['[E001]']
  zx_c6.py:4:17: NameError: [E001] undefined variable 'x'
        g = lambda: x + 1
                    ^
  (lambda never escapes -- still fails)

zx_c9 `def f():\n    y = 10\n    g = lambda: y + 1\n    return g()`  ->
  zx_c9.py:5:17: NameError: [E001] undefined variable 'y'
  (enclosing LOCAL, not just params, is invisible)

zx_c8 `def f(x):\n    g = lambda y: y + x\n    return g(1)`  ->
  zx_c8.py:4:23: NameError: [E001] undefined variable 'x'

zx_c7 `def f(x):\n    def g():\n        return x + 1\n    return g` + `h = f(10); print(h())`  ->
  COMPILE: ok / RUN: exit 0 / GOT 11 / VERDICT: PASS
  IR-SIGNALS: anyunbox=80 _abi_=28  IR-LINES: 1118

Call-position variant (same defect, reported as E002):
zx_p1 `def partial(fn, a):\n    return lambda b: fn(a, b)`  ->
  zx_p1.py:6:22: NameError: [E002] undefined function 'fn'
        return lambda b: fn(a, b)
...
```

Minimal repro:

```python
def f(x):
    g = lambda: x + 1
    return g()
print(f(10))

CPython: 11
asmpython (head, --no-pyinbin-fallback):
  COMPILE: FAILED  codes=['[E001]']
  zx_c6.py:4:17: NameError: [E001] undefined variable 'x'

Contrast -- identical program with a nested def instead of a lambda COMPILES AND PASSES:
def f(x):
    def g():
        return x + 1
    return g()
print(f(10))            # -> 11, VERDICT: PASS
```

Cases: `closure_over_multiple.py`, `partial_application_manual.py`, `proj_call_factory.py`

### 24. The runtime's top-level exception handler writes "Unhandled exception: <msg>" to STDOUT with no exception type and no traceback, and exits 1 — so every escaping Python exception silently corrupts the case's stdout  (3)  — *verified*

This is the shared PRESENTATION of the whole H_crash_other exit-1 group, not their root cause (each of those is attributed separately above). Every exit-1 case in this partition is a genuine Python-level exception escaping to top level; the handler prints only `str(exc)` on stdout, never naming the class. That is why triage recorded stderr="" for all five, and why the codes look like opaque crashes. Two consequences for the next workstream: (1) the message text is the fastest possible triage key — 'list index out of range', "'marker'", 'suppressed', "expected ',' in array at 7" each named the defect immediately; (2) because it goes to stdout rather than stderr, any partially-produced output is followed by the error text, so a case that raises late presents as an OUTPUT-DIFF-shaped stdout rather than a clean crash.

Evidence:

```text
Running each exit-1 case with stdout captured (probe.py prints only stderr on crash, which is why triage showed nothing):
  475_dynamic_dict_index_assign  rc=1  stdout='Unhandled exception: marker'                    stderr=''
  type_alias_annotation          rc=1  stdout='Unhandled exception: list index out of range'   stderr=''
  with_suppress_exception        rc=1  stdout='Unhandled exception: suppressed'                stderr=''
  lib_json_parse_array           rc=1  stdout="Unhandled exception: expected ',' in array at 7" stderr=''
  vm_dict_key_through_any        rc=1  stdout='7 \n Unhandled exception: list index out of range' stderr=''
Note the last one: the correct '7' is emitted first, then the error text lands on the same stream.
Harness used: C:\Users\M\AppData\Local\Temp\claude\c--Users-M-Documents-Coding-asmpython\c0b602e3-3b6f-4da7-937b-6dcda67b3a7d\scratchpad\h7_run.py
```

Minimal repro:

```python
raise ValueError('boom')

CPython:   stderr: 'Traceback (most recent call last): ... ValueError: boom', exit 1
asmpython: stdout: 'Unhandled exception: boom', stderr empty, exit 1

(observed via the five cases above; no exception class name is ever printed)
```

Cases: `475_dynamic_dict_index_assign.py`, `type_alias_annotation.py`, `with_suppress_exception.py`

### 25. list.append on a parameter-bound receiver emits no _abi_list_append at all; the mutation is silently dropped  (3)  — *verified*

When a call site passes a list *variable* (rather than a list literal), the callee's parameter keeps an unresolved/`any` element type, and `param.append(x)` in the callee body lowers to **nothing** — no `_abi_list_append` instruction is emitted for that statement. The list object itself is passed correctly (elements that were already in it are visible inside the callee), so the object is shared, not copied; only the append vanishes. The symptom is a list that never grows — for the caller, for the returned value, and for the callee's own subsequent reads of len()/repr. Passing a literal `[]` at the call site resolves the element type concretely and the very same function then appends correctly, which localises the loss to call-site-driven parameter element-type inference.

Evidence:

```text
Probe (Ksnip/K_i_listin.py) — list already holds 9, callee appends 1:
  def f(l):
      l.append(1); print(len(l)); print(l); return l
  x = [9]; y = f(x); print(y); print(x); print(len(y), len(x))
GOT:
  1        <- len INSIDE the callee, immediately after append (CPython: 2)
  [9]      <- repr INSIDE the callee (CPython: [9, 1])
  [9]
  [9]
  1 1

Contrast, same function, literal argument (Ksnip/K_m_listscope.py):
  print(h([]))  ->  [1]   (correct; IR-SIGNALS anyunbox=0, 79 IR lines)
vs variable argument (Ksnip/K_r_listvar.py):
  x = []; print(h(x))  ->  []   (IR-SIGNALS anyunbox=200, 2297 IR lines)

IR emission counts over the pre-dumped archive:
  app_dependency_resolve   list_append=4  new_list=5
  default_none_or_list     list_append=0  new_list=1
  default_arg_evaluated_once list_append=0 new_list=2
All 4 appends in app_dependency_resolve.ir are in main() (building the `deps` dict literal):
  func main() -> i64 {  ||  525: call _abi_list_append, %%t5, %%t6
  func main() -> i64 {  ||  527: call _abi_list_append, %%t5, %%t7
  func main() -> i64 {  ||  533: call _abi_list_append
...
```

Minimal repro:

```python
def h(l):
    l.append(1)
    return l
x = []
print(h(x))   # asmpython: []      CPython: [1]
print(h([]))  # asmpython: [1]     CPython: [1]   <- literal arg works

The literal-vs-variable split is the whole mechanism.
```

Cases: `app_dependency_resolve.py`, `default_arg_evaluated_once.py`, `default_none_or_list.py`

### 26. self-documented placeholder shims return a different object than the API promises, so callers silently compute on the wrong thing  (3)  — *verified*

Several shims are knowing placeholders whose docstrings say so, but they return a wrong-but-type-compatible object rather than failing. `hashlib.sha1()` literally does `return sha256()`; `functools.total_ordering` is `def total_ordering(cls: int) -> int: return cls` (documented 'Stub: returns class unchanged'), so no comparison methods are synthesised and a missing `__ge__` silently yields False instead of raising TypeError; `numbers`' ABCs are not registered against the builtin numeric types, so `isinstance(5, numbers.Number)` is False. The compiler is not involved -- it executes the shim faithfully. There are 281 such self-documented stub/simplified sites across 52 shim files, so this is an enumerable workstream.

Evidence:

```text
Probe q9_hashlib: `h = hashlib.sha1(); print(h.name); h.update(b'hello'); print(h.hexdigest()[:8])`
  GOT 'sha256' / '2cf24dba'   CPY 'sha1' / 'aaf4c61d'
  (2cf24dba... is sha256('hello') -- the digest proves the object identity swap.)
Shim source asmpython/stdlib/hashlib.py:401-405:
  # sha1: 160-bit digest; we alias to sha256 for type compatibility
  # (real sha1 differs but gives compilable code for shape-testing)
  def sha1() -> sha256:
      """Return a new sha256 object (sha1 stub -- same interface)."""
      return sha256()

Probe q8_total_ordering (@functools.total_ordering with __eq__ and __lt__):
  T(20) < T(30)  -> True  (correct, __lt__ is user-defined)
  T(30) >= T(20) -> False (CPython True)   <- __ge__ never synthesised, and the missing dunder yields False rather than TypeError
  T(30) > T(20)  -> True
Shim source asmpython/stdlib/functools.py:58-61: `def total_ordering(cls: int) -> int: ... """Stub: returns class unchanged.""" return cls`

Probe q8_numbers: `print(isinstance(5, numbers.Number)); print(isinstance(5, int))`
  GOT 'False' / 'True'   CPY 'True' / 'True'
...
```

Minimal repro:

```python
import hashlib
h = hashlib.sha1()
print(h.name)                 # asmpython 'sha256'  CPython 'sha1'
h.update(b'hello')
print(h.hexdigest()[:8])      # asmpython '2cf24dba' (=sha256)  CPython 'aaf4c61d' (=sha1)

import numbers
print(isinstance(5, numbers.Number))   # asmpython False, CPython True
print(isinstance(5, int))              # asmpython True == CPython
```

Cases: `class_comparison_total.py`, `lib_hashlib_update.py`, `lib_numbers_check.py`

### 27. Closure conversion drops a variable captured from a grandparent scope, substituting a load from an uninitialized alloca, and indirect call sites are emitted with the source-level arity instead of the lifted arity  (3)  — *verified*

A closure is materialized as a list `[magic, code_addr, captures...]`. Two defects appear together. First, when a lifted function needs a variable captured two scopes up, the intermediate closure does not thread it: the lowerer emits a fresh `alloca`, never stores to it, and appends the garbage load as the capture. Second, lambda-lifting prepends the captures to the callee's parameter list, but the indirect call site is emitted with only the Python-level arguments -- so a callee declared with three parameters is called with one. The captures are read from whatever the register file happens to hold, and one of those garbage words is the function pointer the body then calls, so control jumps to a wild address.

Evidence:

```text
decorator_with_args.ir. The lifted callee declares three parameters:
    func wrap(%%arg_f: i64, %%arg_n: i64, %%arg_x: i64) -> i64
but the indirect call site passes exactly one argument:
    %%t22: i64 = load %%t6
    %%t24: i64 = load %%t2
    %%t23: i64 = call %%t24, %%t22        ; 1 arg into a 3-param callee

The capture of `n` is lost. `repeat(n)` stores its parameter and then builds the closure list without ever appending it:
    store %%arg_n, %%t2                   ; n stored...
    %%t4: ptr = call _abi_new_list, %%t3
    %%t5: i64 = const 790622
    call _abi_list_append, %%t4, %%t5     ; magic
    %%t6: ptr = global_addr deco
    call _abi_list_append, %%t4, %%t6
    %%t7: ptr = global_addr wrap
    call _abi_list_append, %%t4, %%t7     ; ...n never appended

And `deco` fills the missing capture slot from an alloca it never wrote:
    %%t9: ptr = alloca
    %%t10: i64 = load %%t9                ; uninitialized read
    call _abi_list_append, %%t5, %%t10

In 470_static_class_registry.ir the same construct degrades further -- `register_type`, whose body is `def decorate(cls)
...
```

Minimal repro:

```python
Two-level closure factory -- crashes:

def repeat(n):
    def deco(f):
        def wrap(x):
            r = x
            for _ in range(n):
                r = f(r)
            return r
        return wrap
    return deco
@repeat(3)
def inc(x):
    return x + 1
print(inc(0))

Observed: EXIT 3221225477 (0xc0000005), STDOUT 0 bytes.  CPython: 3

Control -- a single-level closure, called directly, is fine, which isolates the defect to the grandparent capture / indirect arity rather than to closures generally:

def outer():
    def inner(x):
        return x + 1
    return inner
f = outer()
print(f(1))

Observed: EXIT 0.  CPython: 2
```

Cases: `470_static_class_registry.py`, `decorator_preserve_result.py`, `decorator_with_args.py`

### 28. an indirect call through a function-valued name returns garbage instead of the callee's result  (3)  — *verified*

Binding a function to a name and calling through that name breaks the return path: the direct call returns the correct value, the indirect call through the identical function object returns garbage (0xFFFFFFFF here, const 0 or a raw pointer in the corpus cases). This is the declared-signature-vs-indirect-call-contract mismatch already recorded in PHASE1.md, but reproduced here with no lists, lambdas or comprehensions involved — two lines. Everything that reaches a callee as a callable value inherits it: functions stored in a list and invoked in a comprehension (callback_registry, closure_default_arg_capture) and functions passed as a parameter and invoked (compat_dynamic_parameter).

Evidence:

```text
Probe (Ksnip/K_ae_indirect2.py):
  def g(i=7):
      return i
  print(g())    ->  7            (correct)
  h = g
  print(h())    ->  4294967295   (garbage; CPython: 7)
IR-SIGNALS for that 5-line file: anyunbox=80  _abi_new_box=0  const 0=32  947 IR lines — the 'unboxes everywhere, boxes nothing' signature.

Corpus symptoms, same shape:
  callback_registry.py      [cb() for cb in callbacks]     -> [0, 0]   (want ['h1','h2'])
  compat_dynamic_parameter  call_it(shout, 'hi')           -> 0       (want 'HI')
  closure_default_arg_capture [f() for f in fs]            -> [0,0,0] (want [0,1,2])
_ir/callback_registry.ir shows list_append=2 — the two `callbacks.append(fn)` DO emit, so the registry is populated; the loss is at the call.
```

Minimal repro:

```python
def g(i=7):
    return i
print(g())   # asmpython: 7
h = g
print(h())   # asmpython: 4294967295   CPython: 7
```

Cases: `callback_registry.py`, `closure_default_arg_capture.py`, `compat_dynamic_parameter.py`

### 29. The element-kind tag is depth-1 and non-recursive: a container records its element's kind but not that element's own element/value kind  (3)  — *verified*

`list_el_type` is a single scalar string per container node; there is a second slot (`list_el_value_type`, `inner_value_type`) for one more level, and nothing beyond. So a list-of-lists-of-lists, or a list whose elements are dicts, has no place to record the innermost kind, and every read of it falls through `getattr(e, "list_el_type", "int") or "int"` to `int`. The innermost pointers are then formatted as decimal integers while the outer levels still print correctly — producing the characteristic 'correct brackets, integer leaves' output. This is a representational limit of the side channel, not a missing propagation rule, so it cannot be fixed by adding another propagation site.

Evidence:

```text
Nesting depth is the sole variable in this pair:
  a1  print([[1, 2], [3, 4]])   -> [[1, 2], [3, 4]]   CORRECT (depth 2)
  a2  print([[[1, 2]]])         -> [[5776400]]        WRONG   (depth 3)
  a4  print([[[i + j + k for k in range(2)] for j in range(2)] for i in range(2)])
        -> [[7152848, 7152992], [7155312, 7155360]]   WRONG
        (outer two levels print as lists; the third level prints as integers)

Same limit reached one level down through a dict element:
  j9  items = []\nitems.append({'task': 'a', 'done': False})\nprint([i['task'] for i in items])
        -> [5368741893]                                WRONG (CPython: ['a'])
  j13 people = [{'name': 'C'}, {'name': 'A'}]\nprint([p['name'] for p in people])
        -> [5368741893, 5368741895]                    WRONG (CPython: ['C', 'A'])

IR confirms the outer tag is right while the inner is absent —
_ir/deeply_nested_comprehension.ir passes kind=3 (list) to _abi_list_repr,
yet the leaves still print as integers.
```

Minimal repro:

```python
print([[1, 2], [3, 4]])   # asmpython: [[1, 2], [3, 4]]   correct at depth 2
print([[[1, 2]]])         # asmpython: [[5776400]]
                          # CPython:   [[[1, 2]]]

# one level of nesting through a dict is equally fatal
people = [{'name': 'C'}, {'name': 'A'}]
print([p['name'] for p in people])   # asmpython: [5368741893, 5368741895]
                                     # CPython:   ['C', 'A']
```

Cases: `data_sort_by_key.py`, `deeply_nested_comprehension.py`, `int_prog_todo.py`

### 30. A container built by statements inside a function loses its element/value kind at the return boundary, so the caller reads elements at the default `int` kind  (3)  — *verified*

The `list_el_type` side channel lives on expression nodes, not on a function's return type. When a function creates an empty container and fills it with `append`/`__setitem__` statements, the returned value's node at the *call site* carries no `list_el_type`, so every read site falls back to `"int"` via `getattr(..., "int") or "int"`. The element pointer is then either formatted as a decimal integer or fed into integer arithmetic. Annotating the parameter does not help, because the missing attribute is on the call-result node, not on the parameter. A container returned as a syntactic *literal* survives, which is what isolates the defect to the statement-built path.

Evidence:

```text
Probe results (all at 450068a5):
  k5  def f():\n    d = {}\n    d['k'] = 'v'\n    return d\nprint(f()['k'])
      -> 5368750082                     WRONG (CPython: v)
  f2  def pg(items):\n    p = []\n    p.append(items[0:2])\n    return p\nprint(pg([0, 1, 2])[0])
      -> 8332288                        WRONG (CPython: [0, 1])
  k7  same as f2 but `def pg(items: list)`  -> 22422528   STILL WRONG
      (annotation does not rescue it)

Contrast — the same work done at module scope, or returned as a literal, is correct:
  j11 d = {}\nfor line in 'a=1\\nb=2'.split('\\n'):\n    k, v = line.split('=', 1)\n    d[k.strip()] = v.strip()\nprint(d['a'])
      -> 1                              CORRECT
  j2  def f():\n    return [[1, 2], [3, 4]]\nr = f()\nprint(r[0])
      -> [1, 2]                         CORRECT (literal return)
  g1  def r(m):\n    g = []\n    g.append(m)\n    return g\nprint(r('a'))
      -> ['a']                          CORRECT (whole-container repr, not an element read)

j11 vs int_prog_parser is the decisive pair: the identical loop is correct at module
scope and wrong o
...
```

Minimal repro:

```python
# dict built by statements inside a function, read at the call site
def f():
    d = {}
    d['k'] = 'v'
    return d
print(f()['k'])        # asmpython: 5368750082
                       # CPython:   v

# list built by append inside a function, indexed at the call site.
# Annotating `items: list` does NOT fix it.
def pg(items):
    p = []
    p.append(items[0:2])
    return p
print(pg([0, 1, 2])[0])  # asmpython: 8332288
                         # CPython:   [0, 1]
```

Cases: `app_pagination.py`, `app_validate_form.py`, `int_prog_parser.py`

### 31. 11 failing cases have expect blocks that CPython itself does not satisfy, so they cannot pass regardless of compiler correctness  (3)  — *verified*

Two independent test-data defects. (1) `parse_expect` collects every consecutive `#` line after the `# expect:` marker, so a descriptive prose comment written directly under the expected output is absorbed as required stdout — the case demands the compiler print its own rationale. (2) Several `4xx_*` cases spell booleans as `1`/`0` in the expect block, apparently frozen from an era when the compiler printed 1/0; asmpython now prints `True`/`False` — the CPython-correct answer — and is marked failing for being right. I measured this across the whole failing corpus with CPython alone (no compiler involved): 11 of 330 cases fail their own expect block.

Evidence:

```text
Corpus-wide CPython-vs-expect sweep (K_cpy_all.py, pure CPython):
  total failing cases   : 330
  CPython MATCHES expect: 318
  CPython FAILS  expect : 11
  skipped/err           : 1

  BAD-EXPECT 296_collections_namedtuple.py    cpython-rc=1
  BAD-EXPECT 211_argparse_module.py           cpython-rc=1
  BAD-EXPECT 464_metaclass_keyword.py         cpython-output-differs
  BAD-EXPECT 468_provider_type_runtime.py     cpython-output-differs
  BAD-EXPECT 470_global_property_return.py    cpython-output-differs
  BAD-EXPECT 469_guarded_class_string.py      cpython-output-differs
  BAD-EXPECT 473_chained_property_method.py   cpython-output-differs
  BAD-EXPECT 474_boolop_value_flow.py         cpython-output-differs
  BAD-EXPECT 75_assembly_func.py              cpython-rc=1
  BAD-EXPECT lib_calendar_monthrange.py       cpython-output-differs
  BAD-EXPECT ospath_join.py                   cpython-output-differs

Detail for the four in my partition:
  464_metaclass_keyword  cpython '42'          expect '42\nValid Python class-header keyword regression.'
  470_global_property_return cpython 'True'
...
```

Minimal repro:

```python
No compiler needed. Run any of the listed cases under CPython and diff against its own `# expect:` block:

  python diag/tests/cases/473_chained_property_method.py
  -> True
  expect block says: 1

asmpython's output for 470 and 473 is 'True', i.e. it already agrees with CPython. These two are counted as compiler failures while the compiler is correct.
```

Cases: `464_metaclass_keyword.py`, `470_global_property_return.py`, `473_chained_property_method.py`

### 32. stdlib predicate shims are declared `-> int` and return literal 1/0, so every bool-valued stdlib result prints as 1/0  (3)  — *verified*

The pure-Python stdlib shims in `asmpython/stdlib/` declare boolean predicates with an `int` return annotation and return integer literals: `def isleap(year: int) -> int: ... return 1`, `def fnmatch(name: str, pat: str) -> int`, `def truth(a: int) -> int: return 1 if a else 0`. The compiler faithfully materialises the declared type, so `print()` formats an int. This is purely a BINDING defect: I probed the core compiler and `-> bool` works perfectly, including through a function return and through a conditional expression. The shim authors were not forced into `-> int` by any compiler limitation.

Evidence:

```text
Probe q1_ret_bool: `def f(x: int) -> bool: return x > 0` / `print(f(1), f(-1))` -> GOT 'True False', CPY 'True False'.
Probe q1_ret_bool_literal: `-> bool` + `return True if x > 0 else False` -> GOT 'True False'.
Probe q1_ret_int_literal: `-> int` + `return 1 if x > 0 else 0` -> GOT '1 0' (matches CPython for that source, and matches the shim shape).
Shim source asmpython/stdlib/calendar.py:28 `def isleap(year: int) -> int:` then `return 1`.
Shim source asmpython/stdlib/fnmatch.py:61 `def fnmatch(name: str, pat: str) -> int:`.
Shim source asmpython/stdlib/operator.py:121 `def truth(a: int) -> int: return 1 if a else 0`.
Probe qa_bool_shims (all three standalone): GOT '1 0\n1\n0 1', CPY 'True False\nTrue\nFalse True'.
```

Minimal repro:

```python
import calendar, fnmatch, operator
print(calendar.isleap(2020), calendar.isleap(2021))
print(fnmatch.fnmatch('file.txt', '*.txt'))
print(operator.truth(0), operator.truth([1]))
# asmpython: '1 0' / '1' / '0 1'
# CPython:   'True False' / 'True' / 'False True'
# Control (proves the core compiler is fine):
def f(x: int) -> bool:
    return x > 0
print(f(1), f(-1))   # asmpython 'True False' == CPython
```

Cases: `lib_calendar_isleap.py`, `lib_fnmatch.py`, `lib_operator_truth.py`

### 33. `except ... as e` binds e to the raise-site message string, not an exception object, so type(e) and user __str__ are unreachable  (3)  — *verified*

The runtime models a raised exception with exactly two globals — `_runtime_exc_msg` (ptr to the message string) and `_runtime_exc_type` (a match tag). `except X as e` lowers `e` to a load of `_runtime_exc_msg`, so `e` is statically a `str`. Consequently `type(e).__name__` folds to the literal "str" for every exception class, and `str(e)`/`print(e)` yield the raise-site message text rather than dispatching a user-defined `__str__`. A class deriving from `Exception` contributes nothing at runtime beyond the type tag used for matching, so `__str__` defined on it is compiled but never called.

Evidence:

```text
Probe (Ksnip/K_b_exc.py):
  try:
      raise ValueError('v')
  except Exception as e:
      print(type(e).__name__); print(str(e)); print(e)
GOT:
  str
  v
  v

IR (_ir/except_hierarchy.ir) — the ONLY exception state in the whole module:
  15:    %%t6: ptr = global_addr _runtime_exc_msg
  18:    %%t8: ptr = global_addr _runtime_exc_type
  53:    %%t76: ptr = global_addr _runtime_exc_msg
  61:    %%t61: ptr = global_addr _runtime_exc_msg
There is no exception instance allocation anywhere. _ir/exc_custom_str.ir likewise shows only `call _abi_raise, %%t17, %%t16` (type tag + message) and never calls AppError____str__.
```

Minimal repro:

```python
try:
    raise ValueError('v')
except Exception as e:
    print(type(e).__name__)
    print(str(e))

asmpython: 'str' / 'v'
CPython  : 'ValueError' / 'v'

And for exc_custom_str (no message argument, so _runtime_exc_msg is empty):
class AppError(Exception):
    def __str__(self):
        return 'custom message'
try:
    raise AppError()
except AppError as e:
    print(str(e))
asmpython: ''   CPython: 'custom message'
```

Cases: `exc_custom_hierarchy.py`, `exc_custom_str.py`, `except_hierarchy.py`

### 34. the random module binds to the C runtime LCG (srand/rand), not the Mersenne Twister, so every seeded-random expectation is unreachable by construction  (3)  — *verified*

asmpython/stdlib/random.py is not a Python shim at all but a BINDINGS table mapping seed->srand and rand->rand, with the higher-level entry points implemented as inline NASM helpers over that LCG. Its own module docstring says so. CPython's expected values in these three cases are draws from the Mersenne Twister for seeds 42/0/1. No compiler fix can reconcile them; only reimplementing MT19937, or re-authoring the cases to not assert a specific stream, would. This is the corpus-design fact the partition brief anticipated -- reporting it as such rather than as a defect.

Evidence:

```text
asmpython/stdlib/random.py module docstring, line 1:
  """random module: pseudo-random numbers via the C stdlib LCG (rand/srand).

  Higher-level functions (_random_random, _random_randint, _random_uniform)
  are implemented as inline NASM helpers in the target subclasses ..."""
BINDINGS table:
  "seed":     Func(arg_types=("int",), ret_type="int", c_name="srand"),
  "rand":     Func(arg_types=(),       ret_type="int", c_name="rand"),
  "RAND_MAX": Const(ty="int", value=32767),   # Windows CRT RAND_MAX
  "randint":  Func(..., c_name="_random_randint"),
  "shuffle":  Func(..., c_name="_random_shuffle"),
  "sample":   Func(..., c_name="_random_sample"),
triage diffs are all plausible LCG draws, not corrupted values:
  lib_random_seeded  got '76'            want '82'
  lib_random_sample  got '[6, 8, 9]'     want '[0, 6, 9]'
  lib_random_shuffle got '[3, 1, 5, 4, 2]' want '[3, 4, 5, 1, 2]'
IR is tiny and clean in all three (29-40 lines, no anyunbox, no const0 stubs), i.e. nothing is being mis-lowered.
```

Minimal repro:

```python
import random
random.seed(42)
print(random.randint(1, 100))
# asmpython: 76   (C runtime LCG: srand(42)/rand())
# CPython:   82   (Mersenne Twister MT19937)
# Not a defect: the two generators are different algorithms. The corpus case
# asserts a CPython MT draw, which this binding can never produce.
```

Cases: `lib_random_sample.py`, `lib_random_seeded.py`, `lib_random_shuffle.py`

### 35. `None` stored in a container renders as `0`  (3)  — *verified*

`None` is `IntLit(0)` plus an `is_none` side-channel flag that does not survive being stored. Inside a container nothing distinguishes it from the integer 0. PHASE1.md records that a runtime-only fix was tried and regressed 4 cases, because raw unboxed 0s reach the same formatter.

Evidence:

```text
print([1, None]) -> [1, 0]
```

Minimal repro:

```python
print([1, None])
# asmpython [1, 0]   CPython [1, None]
```

Cases: `repr_none_in_list.py`, `vm_container_heterogeneous.py`, `vm_none_is_not_zero.py`

### 36. C's 3-digit exponent: `e+004` where CPython writes `e+04`  (3)  — *verified*

Exponent formatting goes through the C library, which pads the exponent to three digits. Affects `:e`, `:E`, `:.Ne`, `:g` in both directions and `%e`.

Evidence:

```text
f'{12345.678:e}' -> 1.234568e+004   (CPython 1.234568e+04)
```

Minimal repro:

```python
print(f'{12345.678:e}')
# asmpython 1.234568e+004   CPython 1.234568e+04
```

Cases: `float_scientific_upper.py`, `format_general_g.py`, `fstring_exp.py`

### 37. The `%` format-spec presentation type is unimplemented; the raw float is printed  (3)  — *verified*

`{:%}` should multiply by 100 and append `%`. The spec is parsed but the presentation type is ignored, so the underlying float is formatted instead.

Evidence:

```text
f'{0.25:%}' -> 0.25   (CPython 25.000000%)
```

Minimal repro:

```python
print(f'{0.25:.1%}')
# asmpython 0.25   CPython 25.0%
```

Cases: `format_spec_percent.py`, `fstring_percent.py`, `fstring_percent_format.py`

### 38. A function reached only as a first-class value gets no argument types, so its unannotated params and its call result both fall back to `int`  (2)  — *verified*

sema infers an unannotated parameter's type from its direct call sites. When a function is only ever *referenced* (passed to `partial`, to a higher-order function, or applied as a decorator), there is no direct call site, so every unannotated parameter — and the result type of calling the parameter that holds it — defaults to `int`. The body is still type-checked, so the moment that `int`-typed value meets a `str` literal the binop checker at sema.py:9458 raises E012. This is a WRONG inference, not a missing operation: `str + str` is fully supported, and annotating the params makes the identical program compile. Note the fix is necessary but not sufficient — the annotated version compiles and then prints a raw pointer, so these cases move COMPILE-FAIL -> OUTPUT-DIFF, not to PASS.

Evidence:

```text
Probe Pa (mine, minimal): `def up(s): return s + '!'` / `def apply(f): return 'x' + f('hi')` / `print(apply(up))`
  COMPILE: FAILED codes=['[E012]']
  tmpnalf2zel.py:3:14: TypeError: [E012] unsupported operand type for +: int + str
      return s + '!'
               ^
Note the error lands on `s` INSIDE `up` — the param of the referenced function, typed int.

Probe Pc (partial, unannotated): same shape ->
  tmp3j3jgngk.py:4:14: TypeError: [E012] unsupported operand type for +: int + str
      return a + '-' + b

Probe Pd (identical program, params annotated `a: str, b: str`) -> COMPILE: ok, RUN: exit 0, GOT `6106080` (a raw pointer). So the refusal is purely the inference default; the annotation removes it.

Probe C (`partial(add, 1)` where add's params default to int and the body is `a + b`) -> COMPILE: ok, GOT `9841616`. int+int typechecks, so this shape compiles silently and produces garbage instead of refusing.

multiple_decorators shows the mirror manifestation (result side rather than param side):
  multiple_decorators.py:5:22: TypeError: [E012] unsupported operand type for +:
...
```

Minimal repro:

```python
def up(s):
    return s + '!'
def apply(f):
    return 'x' + f('hi')
print(apply(up))

observed (asmpython @450068a5): COMPILE FAILS
  [E012] unsupported operand type for +: int + str   at `return s + '!'`
CPython: prints `xhi!`

Control that isolates the cause — same program, annotated:
def up(s: str) -> str:
    return s + '!'
...compiles.
```

Cases: `lib_functools_partial_kw.py`, `multiple_decorators.py`

### 39. ordering builtins never perform structured comparison: sorted() lowers to _abi_sort_int over raw pointers, and min() over tuples returns the first item unexamined  (2)  — *verified*

`sorted()` picks a fixed-type ABI sort from the static element type and emits `call _abi_sort_int` even when the elements are instance pointers — so a list of objects is sorted by allocation address, which for sequentially constructed objects is the original order, and the user's `__lt__` (which IS compiled, as `icmp.lt` in the method body) is never invoked. `min()`/`max()` over a list of tuples does not compare lexicographically at all; it returns the first element. The tuple defect is not float-specific — an all-int tuple list fails identically — while scalar `min()` over plain ints or floats is correct. So the common defect is that these builtins only handle scalar element types and silently degrade to a no-op for everything else.

Evidence:

```text
Probe (Ksnip/K_aa_sortlt.py):
  class N:
      def __init__(self, v): self.v = v
      def __lt__(self, o): return self.v < o.v
  xs = [N(3), N(1), N(2)]
  print([n.v for n in sorted(xs)])  -> [3, 1, 2]   (CPython: [1, 2, 3])
IR (_ir/class_lt_sort.ir):
  68:    call _abi_sort_int, %%t29        <- integer sort over instance pointers
  89:    %%t43: i64 = icmp.lt %%t39, %%t42   <- N.__lt__ compiled but never called
  97:    %%t49: i64 = icmp.lt %%t45, %%t48

Probe (Ksnip/K_x_min2.py):
  print(min([(2.0,'a'), (1.0,'b')])[1])  -> a   (CPython: b)   <- returns first
  print(min([(2,'a'),   (1,'b')])[1])    -> a   (CPython: b)   <- not float-specific
  print(min([2.0, 1.0]))                 -> 1.0  CORRECT
  print(min([2, 1]))                     -> 1    CORRECT
```

Minimal repro:

```python
class N:
    def __init__(self, v): self.v = v
    def __lt__(self, o): return self.v < o.v
print([n.v for n in sorted([N(3), N(1), N(2)])])
# asmpython: [3, 1, 2]   CPython: [1, 2, 3]

print(min([(2, 'a'), (1, 'b')])[1])
# asmpython: 'a'         CPython: 'b'
```

Cases: `class_lt_sort.py`, `crash_float_comparison_sort_key.py`

### 40. instance attribute lookup reads a fixed slot and never consults __getattr__ or the data-descriptor protocol, yielding const 0 on a miss  (2)  — *verified*

Attribute access lowers to a direct instance-dict/slot read with a `const 0` default (`_abi_dict_get_default(obj, name, const 0)`). A name that is absent from the instance does not fall back to the class's `__getattr__`, and a class attribute that is a descriptor object is not asked for `__get__`/`__set__`. Both hooks are compiled as ordinary methods and simply never called, so the read produces the graceful-stub `0`. class_getattr_dynamic is reduced and verified; 468_static_data_descriptor is the same shape by IR inspection (Descriptor____get__ / Descriptor____set_name__ exist as functions, and the `item.value` read in main does not call them) but I did not reduce it to its own probe.

Evidence:

```text
Probe (Ksnip/K_z_getattr.py):
  class D:
      def __getattr__(self, name): return 'dyn_' + name
  print(D().foo)   -> 0     (CPython: dyn_foo)
IR-SIGNALS: anyunbox=80  _abi_new_box=0  const 0=33  936 IR lines.

IR (_ir/468_static_data_descriptor.ir) — hooks compiled, never dispatched:
  3:@__classobj_Descriptor: ptr
  37:func Descriptor____set_name__(%%arg_self: ptr, %%arg_owner: ptr, %%arg_name: ptr) -> i64 {
  54:func Descriptor____get__(%%arg_self: ptr, %%arg_instance: ptr, %%arg_owner: i64) -> i64 {
468_static_data_descriptor's observed output is '0' where '7' (the descriptor default) is wanted.
```

Minimal repro:

```python
class D:
    def __getattr__(self, name):
        return 'dyn_' + name
print(D().foo)
# asmpython: 0      CPython: dyn_foo

(468_static_data_descriptor.py is attributed on IR evidence only — its `item.value`
reads 0 instead of the descriptor default 7, and Descriptor.__get__ is never called.)
```

Cases: `468_static_data_descriptor.py`, `class_getattr_dynamic.py`

### 41. the is_bool side-channel flag does not survive tuple-unpacking assignment or a classmethod return, so True/False render as 1/0  (2)  — *verified*

`None`/`bool`/`0` share `IntLit(0)` and are told apart only by side-channel flags (PHASE1.md). I isolated two concrete assignment/return paths that drop `is_bool`. (1) Tuple-unpacking assignment: `a, b, c = True, False, True` leaves a/b/c as plain ints, so every downstream use renders 1/0 — while the identical values bound by single assignments render True/False. (2) A `@classmethod` declared `-> bool` returns an int-rendered value, while a plain function with the identical `-> bool` annotation and the identical `in` expression renders True. The producer is not at fault: `in` over a tuple/list/str/dict, `<`, `not`, `and`/`or` and `str.startswith` all preserve bool-ness at top level.

Evidence:

```text
Probe (Ksnip/K_ad_boolmatrix.py) — producer matrix, all correct except the unpack:
  print("a" in t)        -> True
  print(3 in [1,2,3])    -> True
  print("a" in "abc")    -> True
  x, y = True, False
  print(x)               -> 1      <- BUG (CPython: True)
  print(1 < 2)           -> True
  print(not 0)           -> True
  print(True and True)   -> True
  print("k" in d)        -> True

Probe (Ksnip/K_e_bool2.py) — the exact boolean_expression_complex shape:
  a, b, c = True, False, True
  print(a)                            -> 1
  print(a and b)                      -> 0
  print((a and b) or (c and not b))   -> 1     (CPython: True)
  d = True; e = False; g = True
  print((d and e) or (g and not e))   -> True  <- same expression, single assignments

Probe (Ksnip/K_af_bool3.py) — the classmethod surface:
  def f1(r: str) -> bool: return r in ("a","b")
  print(f1("a"))   -> True
  class C:
      t = ("a","b")
      @classmethod
      def m(cls, r: str) -> bool: return r in cls.t
  print(C.m("a"))  -> 1      <- BUG
  def f2(r: str) -> bool: return r.startswith("a")
  print(f2("ab")
...
```

Minimal repro:

```python
a, b, c = True, False, True
print((a and b) or (c and not b))
# asmpython: 1     CPython: True

d = True; e = False; g = True
print((d and e) or (g and not e))
# asmpython: True  CPython: True   <- single assignment is fine

class C:
    t = ("a", "b")
    @classmethod
    def m(cls, r: str) -> bool:
        return r in cls.t
print(C.m("a"))
# asmpython: 1     CPython: True
```

Cases: `468_provider_type_runtime.py`, `boolean_expression_complex.py`

### 42. shim return annotations state the wrong Python type, and the compiler faithfully materialises the declared type instead of the value's own  (2)  — *verified*

Distinct from an outright stub: these shims compute the right value but declare a return type CPython does not use, and the compiler honours the annotation. `calendar.monthrange` is declared `-> list[int]` and returns `[first_day, days]`, so it prints `[5, 29]` where CPython gives the tuple `(5, 29)`. `statistics.median` is declared `-> float` but for odd-length input returns the element itself (`mid: int`), which the declared float return coerces to `3.0` where CPython returns the unchanged int `3`. Both are BINDING defects: I confirmed the core compiler represents and reprs tuples correctly, including across a function return, so nothing forced `-> list[int]`.

Evidence:

```text
Probe qa_bool_shims (last line): `print(calendar.monthrange(2020, 2))` -> GOT '[5, 29]', CPY '(calendar.SATURDAY, 29)' i.e. (5, 29).
Shim source asmpython/stdlib/calendar.py:70-74:
  def monthrange(year: int, month: int) -> list[int]:
      """Return [weekday_of_first_day, number_of_days] for given year/month."""
      ...
      return [first_day, days]
CONTROL -- tuples are fine in the core compiler:
  q2_tuple_direct `print((5, 29))`                       -> '(5, 29)' == CPython
  q2_tuple_ret    `def f() -> tuple: return (5, 29)`     -> '(5, 29)' == CPython
  q2_tuple_var    `t = (5, 29); print(t)`                -> '(5, 29)' == CPython

Probe q8_median: `print(statistics.median([1, 3, 2, 5, 4]))` -> GOT '3.0', CPY '3'.
Shim source asmpython/stdlib/statistics.py:65-73:
  def median(data: list) -> float:
      ...
      if n % 2 == 1:
          mid: int = sorted_data[n // 2]
          return mid          <- int value, coerced by the `-> float` annotation
```

Minimal repro:

```python
import calendar
print(calendar.monthrange(2020, 2))
# asmpython: [5, 29]     (list)
# CPython:   (5, 29)     (tuple)

import statistics
print(statistics.median([1, 3, 2, 5, 4]))
# asmpython: 3.0
# CPython:   3

# control -- tuples work fine, nothing forced the list annotation:
def f() -> tuple:
    return (5, 29)
print(f())   # asmpython '(5, 29)' == CPython
```

Cases: `lib_calendar_monthrange.py`, `lib_statistics_median.py`

### 43. A refused call still binds its target name as undefined, emitting a cascading E001 that inflates the triage code counts  (2)  — *verified*

When sema refuses a call with E021/E022, the assignment target of that call never gets a binding, so every later reference to it raises a second diagnostic, [E001] name is not defined. The E001 is a pure artifact of the earlier refusal, not an independent defect. This matters for how the corpus is grouped: five cases in this partition carry codes ['[E001]', '[E021]'] and would be miscounted as having a name-resolution problem in addition to an arity problem. I reproduced the cascade on a two-line snippet where the only real error is the 3-arg type() call. Recovery should bind the target to an error/any type so that downstream uses stay quiet.

Evidence:

```text
Minimal snippet whose only genuine defect is the type() arity:
  COMPILE: FAILED  codes=['[E001]', '[E021]']
  tmp427i2eyb.py:4:5: TypeError: [E021] type() takes 1 argument(s), got 3
    T = type('T', (B,), {})
        ^
  tmp427i2eyb.py:5:7: NameError: [E001] undefined variable 'T'
    print(T)
          ^
The five partition cases carrying the spurious extra code:
  enum_functional.py, lib_collections_counter_subtract.py, lib_csv_dictreader.py,
  lib_types_simplenamespace.py, lib_uuid_int.py
```

Minimal repro:

```python
class B:
    pass
T = type('T', (B,), {})
print(T)

observed (asmpython): TWO diagnostics - [E021] type() takes 1 argument(s), got 3
                      AND a cascading [E001] undefined variable 'T'
CPython:              <class '__main__.T'>  (one real error at most, never a name error)
```

Cases: `enum_functional.py`, `lib_csv_dictreader.py`

### 44. The binop checker has explicit arms for `list + list` and `list * int` but none for tuple, so both fall through to the numeric-only reject  (2)  — *verified*

sema.py has a concatenation arm and a repetition arm that both test `lt == "list"` / `rt == "list"` and return early. Tuple has no corresponding arm, so control reaches the fall-through at sema.py:9530-9536 — `for side, t in (("left", lt), ("right", rt)): if t not in ("int", "float"): raise SemaError(f"unsupported operand type for {e.op}: {t}", ...)` — which produces the one-sided E013 message seen in both cases. This is genuinely unimplemented, not a mis-inference: the type `tuple` is correctly identified, there is simply no code path that builds a new tuple of computed length. The two cases share one code site, so one repair covers both.

Evidence:

```text
Probe Pw — the list equivalents of both failing cases:
  print([1, 2] + [3, 4])
  print([0] * 3)
  COMPILE: ok / RUN: exit 0 / GOT:
    [1, 2, 3, 4]
    [0, 0, 0]

Cases at head:
  tuple_concat.py:3:14: TypeError: [E013] unsupported operand type for +: tuple
  tuple_repeat.py:3:12: TypeError: [E013] unsupported operand type for *: tuple

Source, sema.py:9529-9536 (the arm immediately above handles `[x] * n`):
  # Numeric-only ops; reject lists/dicts/instances.
  for side, t in (("left", lt), ("right", rt)):
      if t not in ("int", "float"):
          raise SemaError(f"unsupported operand type for {e.op}: {t}", e.pos, ErrorCode.E_UNARY_OP_TYPE)

(Aside: E013 is documented in ecodes.txt as "unary operator on wrong type" but is
raised here for a binary op — the code is mislabeled for this site.)
```

Minimal repro:

```python
print((1, 2) + (3, 4))
print((0,) * 3)

observed: COMPILE FAILS, [E013] unsupported operand type for +: tuple
CPython: prints (1, 2, 3, 4) then (0, 0, 0)

The list spellings — [1,2]+[3,4] and [0]*3 — compile and print correctly,
locating the gap as a missing tuple arm rather than a missing runtime.
```

Cases: `tuple_concat.py`, `tuple_repeat.py`

### 45. `functools.reduce`'s shim types its callable parameter as `int` and its accumulator as `object`; the value returned across the indirect call comes back boxed and the caller reads it as an int, printing the box address  (2)  — *verified*

asmpython/stdlib/functools.py declares `def reduce(func: int, iterable: list, initial: object = 0) -> object` with `acc: object = initial` and `acc = func(acc, item)`. The callable crosses the boundary as a raw `int`, and the `object`-typed accumulator round-trips through the indirect call: the callee's declared signature (raw) and the indirect call site's expectation (`any`, boxed) disagree — the contract mismatch already recorded in PHASE1.md. The accumulator that comes back is a box (or a raw str pointer, for the string variant), and because the caller types `reduce(...)` as `int` the print site emits `_abi_int_to_base` on it.

Evidence:

```text
Reducing the shim to user code reproduces exactly:

  def add(a, b):
      return a + b
  def red(func: int, it: list, initial: object = 0) -> object:
      acc: object = initial
      for item in it:
          acc = func(acc, item)
      return acc
  print(red(add, [1, 2, 3, 4, 5]))
  ->  9381056        (a small heap address, not 15)

Corpus print sites:
  _ir/reduce_with_named_function.ir:618  call _abi_int_to_base, %%t227, ...
  _ir/lib_functools_reduce_strings.ir:526 call _abi_int_to_base, %%t175, ...

Control: `def g(x: int) -> object: return x + 1; print(g(4))` prints 5, so the
`-> object` return type alone is not the defect -- the indirect call is.
```

Minimal repro:

```python
from functools import reduce
def add(a, b):
    return a + b
print(reduce(add, [1, 2, 3, 4, 5]))
# asmpython: 5974048       (heap address)
# CPython:   15

# reduced to user code (no stdlib), same shape, same failure:
def red(func: int, it: list, initial: object = 0) -> object:
    acc: object = initial
    for item in it:
        acc = func(acc, item)
    return acc
print(red(add, [1, 2, 3, 4, 5]))
# asmpython: 9381056
# CPython:   15
```

Cases: `lib_functools_reduce_strings.py`, `reduce_with_named_function.py`

### 46. A direct call passes a raw i64 into a parameter the callee declares as `ptr`; the IR permits the i64->ptr narrowing silently, so the callee's first dereference of that parameter faults  (2)  — *verified*

Call lowering does not reconcile the argument's IR kind with the callee's declared parameter kind. Where a callee is declared `(%arg_self: ptr, ...)` the caller may hand it an i64 temp -- a raw integer, a `const 0`, or an unboxed scalar -- and the IR records the mismatch without complaint. The callee then treats the word as an object pointer on its first attribute access (typically `_abi_dict_get_default`) and faults. This is the same missing pointer-kind invariant as the attribute-receiver defect, but at the user-function ABI boundary rather than at a runtime helper, so it needs a separate repair site.

Evidence:

```text
dunder_radd.ir -- callee is declared `func Money____radd__(%%arg_self: ptr, %%arg_other: i64)`, and inside its own body it is called with an i64 in the self slot:
    %%t12: i64 = call _abi_dict_get_default, %%t9, %%t10, %%t11   ; self.v
    %%t13: i64 = load %%t3                                        ; other  (raw int 10)
    %%t14: ptr = call Money____radd__, %%t13, %%t12               ; 10 passed as arg_self: ptr
On entry the callee does `%%t9 = load %%t2` (= 10) and `_abi_dict_get_default(10, "v", 0)` -- address 10 is dereferenced.

469_guarded_class_string.ir, func main -- `resolves` is declared `func resolves(%%arg_value: ptr)` and is handed a const:
    %%t4: i64 = const 0
    %%t5: i64 = call resolves, %%t4

decimal_precision.ir, func main -- arg#1 is i64 into a ptr parameter:
    call Decimal____init__, %%t67, %%t72
    call Decimal____init__, %%t73, %%t78

Mechanical scan of all 291 corpus IRs comparing each direct call site's argument kinds against the callee's declared signature: 15 of 291 files contain at least one i64/f64 argument flowing into a declared `ptr` paramete
...
```

Minimal repro:

```python
class M:
    def __init__(self, v):
        self.v = v
    def __radd__(self, other):
        return self.v + other
print(10 + M(5))

Observed: EXIT 3221225477 (0xc0000005), STDOUT 0 bytes.  CPython: 15

Control that isolates it to the argument kind, not to __radd__ dispatch itself -- removing the `+` from the body (so no raw int is ever passed in the self slot) makes it run:

class M:
    def __radd__(self, other):
        return 99
print(10 + M())

Observed: EXIT 0, prints 99.  CPython: 99
```

Cases: `decimal_precision.py`, `dunder_radd.py`

### 47. A float read back through an `int`-kinded container slot is printed with the integer formatter, emitting its raw IEEE-754 bit pattern as a decimal  (2)  — *verified*

Same depth-1 element-kind tag as the pointer cases, but with a float payload the symptom differs: instead of a heap address the decimal is the exact 64-bit IEEE-754 encoding of the value. A heterogeneous container (bool+int+float, or a dict mixing list and float values) cannot record `float` in its single kind slot, so it falls back to `int` and `_abi_int_to_base` renders the bits. This is diagnostic — the printed integer decodes back to the expected float — which is how these were separated from genuine pointer cases.

Evidence:

```text
Decoding the observed values with struct.unpack('<d', struct.pack('<q', v)):
  crash_float_nested_container got 4609434218613702656 -> 1.5   (want 1.5) EXACT
  crash_bool_int_float_mix     got 4612811918334230530 -> 2.500000000000001 (want 4.5)

Probe reproducing the mixed-list case:
  d6  print(sum([True, 1, 2.5]))
        -> 4612811918334230530   WRONG (CPython: 4.5) — byte-identical to the corpus

Control: a homogeneous float list is correct, so floats themselves are fine —
  d5  print([1.5, 2.5][0])       -> 1.5              CORRECT

Note crash_bool_int_float_mix has a *second* defect layered on: the bits decode to
2.5000000000000013 rather than 4.5, so the accumulation is also wrong. I established
the formatting half only.
```

Minimal repro:

```python
print([1.5, 2.5][0])          # asmpython: 1.5   (homogeneous list: correct)

print(sum([True, 1, 2.5]))    # asmpython: 4612811918334230530
                              # CPython:   4.5
# 4612811918334230530 is the IEEE-754 bit pattern of a float, not a pointer:
#   struct.unpack('<d', struct.pack('<q', 4612811918334230530)) -> 2.500000000000001

data = {'vals': [1.5, 2.5], 'total': 4.0}
print(data['vals'][0])        # asmpython: 4609434218613702656  (== bits of 1.5)
                              # CPython:   1.5
```

Cases: `crash_bool_int_float_mix.py`, `crash_float_nested_container.py`

### 48. `bytes` has no distinct static type -- it is list[int], so bytes values repr as a list of ints  (2)  — *verified*

A bytes literal lowers to a `list[int]`. Length, indexing and iteration are all correct, but there is no distinct static type to dispatch repr on, so `_abi_list_repr` is selected and prints `[104, 101, ...]` instead of `b'hello'`. Any stdlib shim that legitimately returns bytes (base64.b64encode, binascii.hexlify) inherits the wrong repr even though its computed bytes are correct. This confirms the CONTEXT.md note that bytes are NOT absent and that only repr is wrong -- I re-checked at head rather than assuming.

Evidence:

```text
Probe q3_bytes_direct: `print(b'hello')` -> GOT '[104, 101, 108, 108, 111]', CPY "b'hello'".
Probe q3_bytes_var:    `b = b'AB'; print(b)` -> GOT '[65, 66]', CPY "b'AB'".
Probe q9_b64: `r = base64.b64encode(b'hello'); print(r); print(len(r))`
  GOT '[97, 71, 86, 115, 98, 71, 56, 61]' and '8'   <- the encoded bytes are CORRECT (chr of each = 'aGVsbG8='), only repr differs; len is right.
  CPY "b'aGVsbG8='" and '8'.
Probe q9_binascii: `print(binascii.hexlify(b'AB'))` -> GOT '[52, 49, 52, 50]' (= '4142'), CPY "b'4142'".
Corpus scope: 7 tests/cases/*.py have an expect line beginning with a bytes repr.
```

Minimal repro:

```python
print(b'hello')
# asmpython: [104, 101, 108, 108, 111]
# CPython:   b'hello'
import base64
print(base64.b64encode(b'hello'))
# asmpython: [97, 71, 86, 115, 98, 71, 56, 61]   (correct bytes, wrong repr)
# CPython:   b'aGVsbG8='
```

Cases: `lib_base64_encode.py`, `lib_binascii_hexlify.py`

### 49. The `_` grouping flag in a format spec is ignored for the binary presentation type, because `_abi_int_to_binary` takes no grouping argument  (2)  — *verified*

Format specs like `,d` route through `_abi_group_digits` and insert separators correctly, but the `b` presentation type routes to `_abi_int_to_binary`, whose signature has no grouping parameter. The `_` flag is parsed and then dropped, so the digits are emitted ungrouped. A third case misfiled into the pointer partition — `11111111` is a large decimal but is a correct binary rendering, merely missing separators.

Evidence:

```text
d2  print(f'{255:_b}')
        -> 11111111              WRONG (CPython: 1111_1111)

corpus format_spec_grouping got '1,234,567 11111111', want '1,234,567 1111_1111'.
The decimal grouping is already right; only the binary one is not. Its IR calls
both helpers — abi_calls ['_abi_group_digits', '_abi_int_to_base',
'_abi_int_to_binary'] — showing `_abi_group_digits` exists and is simply not
applied on the `_abi_int_to_binary` path.
```

Minimal repro:

```python
print(format(1234567, ','))   # asmpython: 1,234,567   (decimal grouping works)
print(f'{255:_b}')            # asmpython: 11111111
                              # CPython:   1111_1111
```

Cases: `format_binary_grouped.py`, `format_spec_grouping.py`

### 50. `round(x, n)` scales in binary, so exact-tie cases round the wrong way  (2)  — *verified*

The lowering multiplies by 10**n, applies SSE roundsd, and unscales. `2.55*10` is exactly 25.5 in binary and ties-to-even gives 26, where CPython's decimal-correct round gives 2.5. Needs a real dtoa; msvcrt cannot supply one (it rounds half-away-from-zero and zero-fills past 17 significant digits).

Evidence:

```text
round(2.55, 1) -> 2.6 (want 2.5);  round(2.675, 2) -> 2.68 (want 2.67)
```

Minimal repro:

```python
print(round(2.55, 1), round(2.675, 2))
# asmpython 2.6 2.68   CPython 2.5 2.67
```

Cases: `float_rounding_modes.py`, `vm_round_returns_int.py`

### 51. Tuple comparison stops at element 0 and never breaks ties on later elements  (2)  — *verified*

Ordering a sequence of tuples compares only the first element. Equal first elements are left in input order. `key=`, `reverse=`, float ordering and sort stability were checked in the same probe and are all correct, so this is the tuple comparator specifically, not the sort.

Evidence:

```text
sorted([(1,2),(0,9),(1,1)]) -> [(0,9),(1,2),(1,1)]   (CPython [(0,9),(1,1),(1,2)])
```

Minimal repro:

```python
print(sorted([(1, 2), (0, 9), (1, 1)]))
# asmpython [(0, 9), (1, 2), (1, 1)]   CPython [(0, 9), (1, 1), (1, 2)]
```

Cases: `sorted_tuples_multi.py`, `sorted_with_two_keys.py`

### 52. `int` is a wrapping 64-bit integer; there is no arbitrary-precision path  (2)  — *verified*

Results beyond 2**63 either wrap or collapse to 0 rather than promoting.

Evidence:

```text
2**63 -> -9223372036854775808 ;  bignum_power -> 0
```

Minimal repro:

```python
print(2 ** 70)
# asmpython 0   CPython 1180591620717411303424
```

Cases: `int_bignum_pow.py`, `vm_int_is_arbitrary_precision.py`

### 53. Three independent formatting/introspection builtins are unimplemented, each refusing at its own closed-set dispatch site  (2)  — *strong*

These three are grouped only because they share a shape — a closed dispatch table in sema with a raise in the else — NOT because they share a repair. Each needs its own separate implementation. (1) `%`-formatting parses conversion specifiers from a fixed character set and has no mapping-key form, so `%(name)s` fails at the `(`. (2) `str.format()` field parsing accepts a bare index/keyword only, with no attribute or subscript suffix. (3) `vars()` is refused by design as requiring an interpreter — though for a class whose `__init__` assigns a statically known set of fields it is in principle synthesizable. In all three the operand types are inferred correctly; the operation itself is absent.

Evidence:

```text
string_percent_dict.py:3:29: ValueError: [E133] bad format string: unsupported format character '('
  print('%(name)s is %(age)d' % {'name': 'x', 'age': 5})

str_format_nested_field.py:3:15: ValueError: [E152] str.format() attribute/index access in fields (e.g. '{0.attr}', '{0[0]}') is not supported
  print('{0[1]}'.format(['a', 'b', 'c']))

vars_of_instance.py:7:14: RuntimeError: [E149] vars() is not supported: it requires a Python interpreter and cannot be compiled to native code
  print(sorted(vars(C()).items()))

All three diagnostics name the unsupported construct explicitly and self-describe as
unimplemented. I did not reduce them (they are already 1-3 line cases), so `strong`.
```

Cases: `string_percent_dict.py`, `vars_of_instance.py`

---

## Single-case causes

| case | root cause | confidence |
|---|---|---|
| `462_json_dumps_options.py` | `str` is a UTF-8 byte string, not a code-point sequence, so json.dumps(ensure_ascii=True) escapes each UTF-8 byte separately: "é" becomes Ã© instead of é | verified |
| `469_guarded_class_string.py` | A class named in a value position lowers to `const 0` (the `@__classobj_X` global is stored but never loaded back), and `isinstance(x, type)` discards its computed tag and branches on `const 0` | verified |
| `474_boolop_value_flow.py` | 474_boolop_value_flow asserts an expect block that no Python implementation can produce -- asmpython's output already matches CPython exactly | verified |
| `479_dynamic_classvar_reads.py` | An inherited classmethod call mangles the symbol name from the ACCESSING class, emitting a call to a function that is never codegen'd (link failure) | verified |
| `53_dynamic_import.py` | `importlib.import_module` merges a stdlib shim whose body reads `__pyinbin_loader__`, a symbol only the interpreter runtime injects; sema also ignores the `except NameError` guard around it | verified |
| `75_assembly_func.py` | @assembly_func is refused by design on --backend x86-64 because that backend discards the assembly body instead of erroring | strong |
| `algo_count_islands.py` | Free variables in a nested `def` are typed `int` unless the enclosing binding is a syntactic literal or carries an annotation | verified |
| `bignum_factorial.py` | `int` is a wrapping i64 with no arbitrary-precision path, so large integer results silently truncate (misfiled into this partition by the pointerish heuristic) | verified |
| `bignum_power.py` | integers are fixed-width 64-bit with no arbitrary-precision fallback, so ** silently wraps modulo 2**64 | verified |
| `bytes_from_str.py` | `str.encode()`'s result is not list-kinded, so the returned bytes object prints as a raw pointer rather than as a list | verified |
| `compat_analysis_dynamic_return.py` | A parameter reached by call sites of different types unifies to `int`, so `str(v)` on the str call formats the pointer as a decimal | verified |
| `compat_class_string.py` | a class object has no materialised __name__; reading it yields a NULL pointer | verified |
| `compat_class_value_tuple.py` | A class object read out of a tuple types as the non-instantiable `type`, and binding it to a name degrades it further to `int` | verified |
| `compat_iterable_element_helper.py` | a value returned as a loop element from an untyped sequence parameter is returned raw, so the caller renders a pointer or stubs downstream methods to 0 | verified |
| `compat_ordered_flow_combined.py` | An element read out of a list of user-class instances is `int`-kinded, so a method called on that element returns an `int`-kinded result | verified |
| `complex_arithmetic_skip.py` | Arithmetic dunders are absent from the builtin int method table; the receiver kind is correct, so this is a missing method and not a lattice conflation | verified |
| `complex_literal.py` | No complex/imaginary numeric type exists; the number scanner stops at the digits and the trailing 'j' lexes as a separate NAME | verified |
| `conditional_function_selection.py` | A builtin captured as a value and called indirectly is not modelled; the indirect call yields a pointer instead of the builtin's result | verified |
| `conditional_import_pattern.py` | `import` of an unknown module inside try/except is a compile-time no-op, so the ImportError branch is dead and the guard reports the module as present | verified |
| `crash_conditional_type_change.py` | if/else branches assigning different static types to one name produce a garbage merge — the name reads an unrelated constant or a raw pointer | verified |
| `crash_float_format_edge.py` | the float runtime delegates to msvcrt and inherits C semantics wherever Python's differ (fmod truncation, 3-digit %e exponent) | verified |
| `crash_nested_function_float.py` | a float whose type is only inferred (unannotated parameter default, or a captured free variable) is given an i64 slot, so the double's bits are reinterpreted as a denormal | verified |
| `crash_recursive_float.py` | Call sites pass an argument's raw 64 bits into a parameter of a different resolved type, with no int/float/ptr conversion — a float arg lands in the callee's int slot | verified |
| `dispatch_table_class_methods.py` | A bound method (`self.m`) has no first-class callable representation: stored in a container it degrades to `any`, bound to a local it compiles to code that segfaults | verified |
| `double_star_merge_call.py` | A call site accepts at most one **expr argument, and behind that restriction **dict_var expansion compiles but crashes at runtime | verified |
| `dunder_format.py` | A user-defined `__format__` is not dispatched from an f-string; the raw instance pointer reaches `_abi_fmt_elem` and is printed as a decimal | verified |
| `dunder_getitem_slice.py` | A slice subscript on a user class is never routed to `__getitem__`; only an integer subscript is | verified |
| `dunder_iadd.py` | augmented assignment on an instance lowers to a raw integer add on the object pointer instead of dispatching __iadd__ | strong |
| `exc_type_error.py` | A statically-detected operand type error is always a hard compile refusal; sema has no lowering to a runtime `TypeError` raise, so programs that deliberately trigger and catch one cannot compile | verified |
| `except_multiple_types.py` | Class/type objects are not first-class values: a list whose elements are exception classes is rejected by the list-element type whitelist | strong |
| `filter_returns_iterator.py` | filter() does not produce an object next() can advance; the next() dispatch falls into a branch that yields const 0 | strong |
| `float_percentage_func.py` | An unannotated function whose body returns a float is typed as returning an integer, so the caller reads the integer return register while the callee returned in xmm0 | verified |
| `format_align_equals.py` | `=` alignment in a format spec is ignored | verified |
| `generator_class_iterator.py` | The iterable-protocol check never consults __iter__, so a class whose __iter__ is a generator is classified as non-iterable | verified |
| `generator_pipeline.py` | Only the outermost generator in a pipeline gets its `__iter__`/`__next__` state machine emitted; a generator consumed as another generator's parameter has just an `__init__` | verified |
| `int_negative_shift.py` | Right-shift of a negative int lowers to a logical shift (SHR) instead of an arithmetic shift (SAR), so the sign bit is not replicated | verified |
| `int_prog_csv_aggregate.py` | for-loop tuple unpacking binds loop variables at the default `int` kind, which silently degrades `int(v)` into a no-op that stores a str pointer | verified |
| `int_prog_priority_queue.py` | `list.pop()` returns a value with the element-kind tag dropped, so a tuple pulled out of a list reads its fields at the default `int` kind | verified |
| `int_prog_tokenizer.py` | Heterogeneous appends collapse a list's element-kind tag to the first-written kind, storing later elements raw | verified |
| `lambda_default_arg.py` | Default argument values are materialized for `def` but not for `lambda`; an omitted lambda argument reads uninitialized storage | verified |
| `lib_cmath_sqrt.py` | The cmath shim defines a Python function named `sqrt` in the same module that calls libc `sqrt`, and the two bind to one symbol | strong |
| `lib_collections_defaultdict.py` | dict() accepts only a real dict or list-of-pairs, so shim classes that emulate a builtin container cannot be converted back to it | verified |
| `lib_collections_userdict.py` | Subscript protocol lookup reads only the class's OWN method table and never walks its bases, so any subclass of a container class loses `__getitem__`/`__setitem__` | verified |
| `lib_configparser.py` | The configparser shim deliberately avoids the mapping protocol — its accessors are named `get_option`/`getint_option` and no class defines `__getitem__` | strong |
| `lib_contextlib_manager.py` | `contextlib.contextmanager` is an identity stub, so a decorated generator stays a generator object and fails the `with` protocol check | strong |
| `lib_difflib_close.py` | list.sort()/sorted() is a silent no-op when the element type is a list -- no comparator exists for list elements and the fallback does nothing instead of erroring | verified |
| `lib_enum_iteration.py` | `for` and comprehension iteration are whitelists over range/list/dict/tuple/str, with no arm for an Enum class | verified |
| `lib_functools_cmp.py` | key= is matched syntactically and rejects any call-expression result, and functools.cmp_to_key is a no-op stub that would not adapt cmp arity even if accepted | verified |
| `lib_functools_reduce_initial.py` | `functools.reduce` over an empty sequence returns uninitialized storage instead of the supplied initial value | verified |
| `lib_glob_pattern_match.py` | the used-function walk does not descend into a comprehension's `if` clause, so a function called only from a filter is emitted as a `ret 0` stub | verified |
| `lib_gzip_roundtrip.py` | `bytes` literals are modeled as `list[int]` while the binary stdlib shims are declared to take and return `str`, so any bytes round-trip is a static type clash | verified |
| `lib_hmac_new.py` | Whole-program flattening skips the `M.f()` -> `f()` rewrite when two merged modules define the same top-level name, and the unrewritten call is misreported as E005 'no module M is available' | verified |
| `lib_itertools_accumulate_func.py` | itertools shims are semantically wrong independently of any boxing bug: they return lists where CPython returns tuples, and `accumulate`'s second parameter is `initial: int`, not a binary function | verified |
| `lib_json_nested.py` | `json.loads`' result type is chosen by a syntactic pattern-match on the call argument, so binding the JSON text to a variable first falls back to the scalar `-> str` parser | verified |
| `lib_json_parse_array.py` | The bundled json parser is non-recursive: `loads_list`/`loads_dict` have no `[` or `{` case in their value dispatch, so any nested container falls into the number scanner and dies | verified |
| `lib_operator_contains.py` | The operator-module shim monomorphises `contains` to the str implementation, so a list receiver is passed to _abi_str_index_of | verified |
| `lib_pickle_roundtrip.py` | dict equality compares list-valued entries by raw pointer, so structurally equal dicts holding lists compare unequal | verified |
| `lib_random_randrange.py` | The native Func(arg_types=...) binding table can express only one fixed arity per name, so stdlib entries with optional/overloaded parameters are declared at their narrowest form | verified |
| `lib_re_sub_func.py` | A lambda's parameter types are inferred only from its own immediate call site; passed as a value its parameters default to `int` | verified |
| `lib_zlib_crc32.py` | a bytes argument reaching a shim parameter annotated `str` is silently accepted and iterated as list[int], corrupting the result | verified |
| `min_mixed_int_float.py` | A lost kind landing in a float slot is reinterpreted as an IEEE754 double | verified |
| `mixed_int_float_list_sum.py` | `sum()` over an `any`-element list accumulates with integer add and is typed `int`, so a float element's IEEE payload is added as an integer and printed as a decimal | verified |
| `nested_closures.py` | Closure nesting is tracked only one level deep: a nested function that returns another nested function has return type `any`, so the second call is refused | verified |
| `param_mixed_all_kinds.py` | Call-site argument marshalling for a signature mixing *args with keyword-only parameters is off by one slot | verified |
| `proj_event_dispatch.py` | An empty container literal pins its element type to `int`, and later stores never widen it | verified |
| `r39_group_consecutive.py` | A list's element type is resolved in source order and not refined by a later `.append()`, so a use textually before the append sees element type `?` and every operation on the element is refused | verified |
| `repr_string_with_quotes.py` | `repr` of a str never switches quote style for embedded quotes | verified |
| `round_to_negative_places.py` | `round(x, negative)` returns a float where CPython returns an int | verified |
| `set_update_multiple.py` | set.update is modeled at fixed arity 1; the variadic form CPython allows is refused | verified |
| `sim_leaderboard.py` | The `for ... in enumerate(...)` form is special-cased to exactly two flat name targets, so a nested destructuring target is refused | verified |
| `sim_text_adventure.py` | An empty container literal among a container's values unifies to the `int` default instead of to the other branch's type, poisoning every read of that container | verified |
| `slice_step_zero_error.py` | `_runtime_list_slice_step` advances its cursor by the step with no step==0 guard anywhere in sema, ir_lower or the runtime, so `x[::0]` spins forever instead of raising ValueError | verified |
| `sorted_with_none_handling.py` | `sorted()` never consults `__lt__` on instances; the input order is returned | verified |
| `str_encode_errors.py` | `str.encode()` produces a value with no static type, so the returned bytes object is formatted as an integer rather than reaching the list-repr path at all | verified |
| `vm_bool_is_not_int.py` | `bool` loses its identity when stored, rendering as `1`/`0` | strong |
| `vm_callable_param_result.py` | A function used as a value has two incompatible calling conventions | verified |
| `vm_dict_key_through_any.py` | Subscript lowering picks the container operation from the KEY's static type, so `d[k]` on an unannotated parameter with a non-str key becomes a list index into a dict | verified |
| `zip_and_dict.py` | A dict built by `dict(zip(...))` boxes its values on write but the subscript read is typed `int` and emits no unbox, so the box pointer itself is printed in decimal | verified |
| `zip_longest_manual.py` | A conditional whose arms unify to a pointer type erases the None arm to a raw 0, producing a NULL element that the container repr dereferences | verified |

---

## NOT root-caused  (41)

Grouped by where they fail, because attributing them would be guessing. Each carries its measured symptom so the next pass starts from evidence rather than from this document's opinion.

| case | measured symptom | what is known |
|---|---|---|
| `211_argparse_module.py` | compile refused [E012] [E113] | NOT root-caused. Established: the failure is entirely inside `asmpython/stdlib/argparse.py`, not in the 13-line test case -- the reported line/col pairs (439:30, 459:33, 612:45, 619:20) are attributed |
| `476_data_descriptor_precedence.py` | RUN-CRASH 0xc0000005 | Not reduced. `Descriptor(Value(42), value_type=Value)` passes a class as a default argument and the `__get__`/`__set__` data-descriptor protocol drives every access. Its IR does load `@__classobj_*` ( |
| `app_expression_eval.py` | RUN-CRASH 0xc0000005 | Not investigated beyond triage. Builds a genuinely mixed-type list `[2, 3, 4, '*', '+']` and pushes both ints and strs onto one stack. The stale trailing comment claims an [E051] mixed-element-type co |
| `app_json_config.py` | RUN-CRASH 0xc0000005 | Reproduced the AV at head with the case verbatim (EXIT 0xc0000005). Ruled OUT the obvious suspect: `config = {...mixed values...}; node = config['debug']; isinstance(node, dict)` runs clean and exits  |
| `bound_method_frozenset_contains.py` | RUN-CRASH 0xc0000005 | Reproduced with `isk = frozenset(["def","for"]).__contains__; r = isk("def")` -> EXIT 0xc0000005 on the bind-and-call alone. Its IR has 2 indirect `call %%tN` sites, so it plausibly belongs to the clo |
| `conditional_import_fallback.py` | RUN-CRASH 0xc0000005 | Not reduced. Two facts established from the pre-dumped IR only: it declares 9 `@__classobj_*` globals and LOADS none of them (the same store-never-load signature as the class-as-value cause), and it h |
| `float_func_return.py` | want `212.0` got `1` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `float_nan_compare.py` | want `False True` got `True False` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `generator_expr_direct.py` | want `0 1` got `0 0` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `init_subclass_hook.py` | want `['A', 'B']` got `[]` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `int_from_bytes.py` | want `1024` got `0` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `iter_two_arg_sentinel.py` | RUN-CRASH 0xc0000005 | Reproduced verbatim (EXIT 0xc0000005); the direct control `g = lambda: next(it); g()` exits 0, so the two-arg `iter(callable, sentinel)` form is what breaks. IR has zero indirect calls, so my closure  |
| `lambda_capturing_outer_var.py` | want `[30, 30, 30]` got `[0, 0, 0]` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `lib_array_basic.py` | RUN-CRASH 0xc0000005 | Not reduced. `from array import array; a = array('i',[1,2,3]); a[0]`. Its sibling lib_array_typecodes.py DOES carry the non-pointer-receiver signature (9 sites, e.g. receiver `%%t163: i64 = iadd %%t10 |
| `lib_collections_counter_total.py` | RUN-CRASH 0xc0000005 | Not root-caused. My first NULL-receiver scan flagged `_abi_dict_get_default(%%t42)` in Counter____init__ with %%t42 = const 0, but a block-aware re-scan disproved it: temps are numbered per-function,  |
| `lib_collections_counter_update.py` | RUN-CRASH 0xc0000005 | Not root-caused, and the weakest of the set: this is the only case in my partition with no pre-dumped IR in _ir/, and I did not spend a build on it. Nothing established beyond the triage record (RUN-C |
| `lib_collections_ordereddict_move.py` | RUN-CRASH 0xc0000005 | Not root-caused. Shares the structural signature of the attribute-sentinel cause — 9 shim class objects all built as empty `_abi_new_instance`, 22 anytag dispatch chains, 22 dict_get_default calls — b |
| `lib_csv_reader.py` | RUN-CRASH 0xc0000005 | Not root-caused. My NULL-receiver hits (`_abi_str_eq(%%t24)`, `_abi_str_eq(%%t68)` inside _parse_line) were disproved by the block-aware re-scan as cross-function temp-name collisions — those const-0  |
| `lib_datetime_combine.py` | RUN-CRASH 0xc0000005 | Not root-caused. Same situation as ordereddict_move: five empty shim class objects (@__classobj_date, timedelta, ...), 18 anytag chains, one load-before-store alloca at L208/L298. Consistent with eith |
| `lib_itertools_groupby.py` | RUN-CRASH 0xc0000005 | Not root-caused. Same disproof: the three flagged NULL receivers in `groupby/Lwhileend9` (list_append, list_slice, list_extend on %%t96/%%t100) resolve to const-0 defs in `main`'s blocks, not groupby' |
| `lib_operator_methodcaller.py` | RUN-CRASH 0xc0000005 | Not root-caused. Consistent with the attribute-sentinel cause — the shim's @__classobj__MethodCaller is an empty `_abi_new_instance` and `upper('hi')` would be an indirect call through whatever the lo |
| `lib_re_named_groups.py` | RUN-CRASH 0xc0000005 | Not root-caused. The two flagged `_abi_str_eq` NULL receivers were again cross-function temp collisions (defs in _try_match, uses in _match_char_class/_atom_matches) and are disproved. The re engine i |
| `list_comp_with_walrus_call.py` | want `[0, 1, 4]` got `[0, 0, 0, 0]` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `list_slice_assign_resize.py` | want `[1, 9, 4]` got `[1, 9, 3, 4]` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `list_slice_assignment_grow.py` | want `[1, 10, 20, 2, 3]` got `[1, 2, 3]` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `multiple_context_vars.py` | want `[('a', 1), ('b', 2), ('c', 3)]` got `[]` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `nested_dict_comp.py` | want `{0: {0: 0, 1: 0}, 1: {0: 0, 1:` got `{0: {'0': 0, '1': 0}, 1: {'0':` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `nested_dict_default.py` | RUN-CRASH 0xc0000005 | Not root-caused. The odd one out structurally: 30 anytag chains and 8 `_abi_new_box` calls (most cases in this partition box nothing), yet no NULL receiver and no load-before-store alloca. The boxing  |
| `number_sign_function.py` | want `1 -1 0` got `True True False` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `operator_overload_comparison.py` | want `True False` got `True True` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `pow_three_arg.py` | want `24` got `0` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `prog_price_formatter.py` | want `['$9.99', '$19.50', '$100.00']` got `['$9.99', '$19.5', '$100']` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `r39_char_histogram_sort.py` | want `i 4` got `m 1` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `r40_compound_interest.py` | want `1157.62` got `5.131006077194019e+18` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `r40_percentage_change.py` | want `50.0` got `100.0` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `r40_series_sum.py` | want `2.0833` got `24.0` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `repr_mixed_container.py` | want `{'list': [1, 2], 'tup': (3, 4)` got `{'list': [1, 2], 'tup': [3, 4]` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `sim_discount_calc.py` | want `[80.0, 40.0, 160.0]` got `[100.0, 100.0, 100.0]` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `str_splitlines_keepends.py` | want `['a\n', 'b\n']` got `['a', 'b']` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `temperature_convert.py` | want `212.0 32.0 98.6` got `1 1 1` | not investigated: the two `K_out_core` finder slices that owned it did not complete |
| `type_name_lookup.py` | want `list` got `<missing>` | not investigated: the two `K_out_core` finder slices that owned it did not complete |

---

### Cases claimed by more than one cause

Recorded rather than hidden: each was assigned to the first cause listed, and the alternative is a live hypothesis if that assignment turns out wrong.

| case | assigned to | also claimed by |
|---|---|---|
| `462_json_dumps_options.py` | `str` is a UTF-8 byte string, not a code-point sequence, so  | `462_json_dumps_options.py` has no `# expect:` block |
| `468_provider_type_runtime.py` | the is_bool side-channel flag does not survive tuple-unpacki | 11 failing cases have expect blocks that CPython itself does |
| `469_guarded_class_string.py` | A class named in a value position lowers to `const 0` (the ` | A direct call passes a raw i64 into a parameter the callee d |
| `475_dynamic_dict_index_assign.py` | The runtime's top-level exception handler writes "Unhandled  | The data-descriptor write path is missing: attribute assignm |
| `53_dynamic_import.py` | `importlib.import_module` merges a stdlib shim whose body re | Diagnostics raised inside merged stdlib source are stamped w |
| `bignum_factorial.py` | `int` is a wrapping i64 with no arbitrary-precision path, so | `int` is a wrapping 64-bit integer; there is no arbitrary-pr |
| `bignum_power.py` | integers are fixed-width 64-bit with no arbitrary-precision  | `int` is a wrapping 64-bit integer; there is no arbitrary-pr |
| `bytearray_mutate.py` | `bytes` and `bytearray` are the static type `list`, so they  | bytearray/bytes have no distinct static type, so repr render |
| `bytes_decode.py` | Stdlib objects with no distinct static type are represented  | `bytes` and `bytearray` are the static type `list`, so they  |
| `class_comparison_total.py` | self-documented placeholder shims return a different object  | `sorted()` never consults `__lt__` on instances; the input o |
| `class_lt_sort.py` | ordering builtins never perform structured comparison: sorte | `sorted()` never consults `__lt__` on instances; the input o |
| `compat_metaclass_descriptor_collect.py` | Attribute access lowers to `_abi_dict_get_default(recv, name | A direct call passes a raw i64 into a parameter the callee d |
| `complex_number_basic.py` | A refused statement leaves its target unbound, so every late | The `complex` builtin type does not exist in the compiler at |
| `crash_float_default_param.py` | a float whose type is only inferred (unannotated parameter d | Frontend/inference work that exists only on `origin/beta/3.1 |
| `crash_float_format_edge.py` | the float runtime delegates to msvcrt and inherits C semanti | C's 3-digit exponent: `e+004` where CPython writes `e+04` |
| `crash_float_modulo_negative.py` | the float runtime delegates to msvcrt and inherits C semanti | Frontend/inference work that exists only on `origin/beta/3.1 |
| `dict_dict_comprehension.py` | Frontend productions are fixed-shape: clause lists, nested l | Two parser cases have a second, non-frontend blocker behind  |
| `enum_functional.py` | A refused call still binds its target name as undefined, emi | enum.Enum's functional construction API is absent; the shim' |
| `fstring_equals_debug.py` | Frontend productions are fixed-shape: clause lists, nested l | `f'{x=}'` self-documenting expressions are a parse error |
| `function_returning_function.py` | A `lambda` body does not capture enclosing free variables at | Frontend/inference work that exists only on `origin/beta/3.1 |
| `lambda_nested.py` | Frontend productions are fixed-shape: clause lists, nested l | Two parser cases have a second, non-frontend blocker behind  |
| `lib_base64_encode.py` | `bytes` has no distinct static type -- it is list[int], so b | `bytes` and `bytearray` are the static type `list`, so they  |
| `lib_binascii_hexlify.py` | `bytes` has no distinct static type -- it is list[int], so b | `bytes` and `bytearray` are the static type `list`, so they  |
| `lib_collections_counter_subtract.py` | Python-source stdlib shims declare hand-narrowed signatures  | A refused call still binds its target name as undefined, emi |
| `lib_csv_writer.py` | A refused statement leaves its target unbound, so every late | E005 'no module X is available' is emitted for a missing *me |
| `lib_hmac_new.py` | Whole-program flattening skips the `M.f()` -> `f()` rewrite  | Diagnostics raised inside merged stdlib source are stamped w |
| `lib_itertools_combinations.py` | An `any`-element container is read with TAGGED_REPR_KIND (6) | itertools shims are semantically wrong independently of any  |
| `lib_itertools_pairwise.py` | An `any`-element container is read with TAGGED_REPR_KIND (6) | itertools shims are semantically wrong independently of any  |
| `lib_itertools_permutations.py` | An `any`-element container is read with TAGGED_REPR_KIND (6) | itertools shims are semantically wrong independently of any  |
| `lib_itertools_product.py` | The repr-kind word encodes only two levels of container nest | itertools shims are semantically wrong independently of any  |
| `lib_itertools_zip_longest.py` | The repr-kind word encodes only two levels of container nest | itertools shims are semantically wrong independently of any  |
| `lib_json_parse_array.py` | The bundled json parser is non-recursive: `loads_list`/`load | The runtime's top-level exception handler writes "Unhandled  |
| `lib_types_simplenamespace.py` | Python-source stdlib shims declare hand-narrowed signatures  | A refused call still binds its target name as undefined, emi |
| `lib_uuid_int.py` | Python-source stdlib shims declare hand-narrowed signatures  | A refused call still binds its target name as undefined, emi |
| `proj_call_factory.py` | A `lambda` body is name-resolved against globals + its own p | A function passed as a value is never treated as a call site |
| `repr_nested_dict.py` | The repr-kind word encodes only two levels of container nest | Container repr is depth-limited: the third level of nesting  |
| `slice_step_zero_error.py` | `_runtime_list_slice_step` advances its cursor by the step w | `[1,2,3][::0]` hangs forever instead of raising ValueError |
| `str_format_nested_field.py` | Nested / dynamic format specs are emitted literally instead  | Three independent formatting/introspection builtins are unim |
| `syntax_semicolons.py` | Frontend productions are fixed-shape: clause lists, nested l | Frontend/inference work that exists only on `origin/beta/3.1 |
| `type_alias_annotation.py` | The runtime's top-level exception handler writes "Unhandled  | A PEP-484 type alias `V = List[float]` is compiled as a real |
| `vm_dict_key_through_any.py` | Subscript lowering picks the container operation from the KE | The runtime's top-level exception handler writes "Unhandled  |
| `with_suppress_exception.py` | The runtime's top-level exception handler writes "Unhandled  | The `with` lowering calls `__exit__` on the exception path b |

---

## Causes that would each fix more than ten cases

These set the next workstream's priority. Everything below is measured; the
per-cause case lists are in the ranked table.

### P0 — Reconcile the branch. Recovers exactly 38 cases for no compiler work.

Local `beta/3.14.0` is 23 commits behind `origin/beta/3.14.0`, and that gap is
worth more than any single fix in this document. It is also the cheapest: the
work already exists and was already written.

It is not free of judgement — local is *also* 17 commits ahead, including the
`UNKNOWN_TY` split (`821ceb9c`) and the `finally` fix (`f4474ede`), so this is a
merge with real conflicts in `sema.py` and `ir_lower.py`, not a fast-forward.
But nothing else in this audit has a comparable ratio of cases to effort.

**Do this before anything else**, because it moves the baseline that every other
measurement here is relative to.

### P1 — Close the rest of the parameter-inference boundary.

§1 shows the boundary is half-closed on the unmerged branch. Merging fixes
forwarding, local variables, and float/list kinds. It leaves open exactly the
routes real programs use:

```text
arg_list_elem     f(xs[0])        still raw at origin
arg_call_result   f(src())        still raw at origin
arg_field         f(obj.t)        still raw at origin
ret_attr_of_param def g(o): return o.t
comp_call_unann   [f(v) for v in xs]
indirect_bare/ann fn(a) through a parameter
```

This is the cause whose repair has the widest reach, because it is the one that
manifests as raw pointers, access violations, `0`-valued results and compile
refusals simultaneously. **Its exact corpus count is deliberately not asserted**
— see §1's bound — but no other single mechanism spans four failure classes.

`PHASE1.md` §6 already has the design for the indirect-call arm (a function used
as a value adopts a boxed-return convention and all its call sites unbox), and
notes `_is_callable_valued` already identifies the functions.

### P2 — A real `dtoa` in the runtime.

Blocks, at minimum: the 3-digit exponent defect (§2a, six probes), correct
`round(x, n)` (§2d), `round(x, negative)` returning an int (§2e), and the
`%f`-accuracy note in `PHASE1.md` §2. msvcrt cannot supply it — it rounds
half-away-from-zero where CPython rounds half-to-even, and zero-fills past 17
significant digits, so the tie cannot even be detected from its output.

One component; four defects; ~15 cases across the formatting cluster.

### P3 — Make strings code-point sequences, not UTF-8 byte arrays.

Verified in one probe:

```python
s = 'héllo'
print(len(s))     # asmpython 6   CPython 5
print(ord('中'))  # asmpython 228 CPython 20013
print(s[1])       # asmpython a partial byte
```

`len`, `ord`, indexing, slicing and `repr` are all affected wherever a case uses
non-ASCII text.

### Not a >10 cause, but the highest severity per case

**55 access violations.** A wrong answer is a bug; a program that dereferences a
raw integer is a memory-safety defect reachable from ordinary Python. The
previous audit had no crash category at all. `slice_step_zero_error.py` is worse
still — `[1,2,3][::0]` does not raise `ValueError`, it **hangs forever**.

### One free measurement fix

Make `tests/runner.py` pass `--no-pyinbin-fallback` by default. It costs
nothing — the corpus already cannot pass a case whose artifact was never
written — and it converts 56 uninformative `runner error: [WinError 2]` lines
into real compiler diagnostics.

---

## What changed since the previous audit, entry by entry

The previous audit listed 25 causes over 285 cases. It is superseded rather than
extended, because its tree is a different branch (§0). This records what carried
over, what was **wrong**, and what it could not see — so the reasoning is not
lost and the same mistakes are not repeated.

| previous entry | cases | disposition now |
|---|---:|---|
| NOT root-caused — core language | 64 | **largely attributable.** Formatting (§2), UTF-8 byte strings, tuple comparison, `sorted` ignoring `__lt__`, and the inference boundary (§1) account for most of it. |
| NOT root-caused — stdlib | 39 | **mechanism found**: the `stdlib/*.py` shims declare hand-narrowed signatures that sema then faithfully enforces. |
| boxing: known type recorded as `any` → raw pointer | 39 | **confirmed and relocated.** The symptom is real; the site is not containers (all 18 container sites round-trip correctly) but unannotated parameter boundaries. |
| bytes / bytearray type absent | 30 | **wrong, twice over.** `bytes` is not absent — it is `list[int]`, and works for `len`, indexing and mutation; only `repr` differs. `bytes_decode.py` fails with `unsupported expr MethodCall (list.decode)`, naming the real type. And the *group* was over-attributed: only about 5–8 of the 30 show bytes-related symptoms; the rest are raw pointers, ordering, format specs and crashes. |
| other compile-time refusal | 15 | **understated by 6×.** With the fallback disabled there are 98 native refusals. |
| corpus defect — the test itself is wrong | 11 | partly confirmed; `462_json_dumps_options.py` genuinely has no `# expect:` block. |
| stdlib bindings: wrong signature | 11 | confirmed, with the mechanism now named (hand-narrowed shim signatures; a fixed-arity `Func(arg_types=…)` table). |
| operator / indexing / iteration protocol gap | 9 | confirmed. |
| sema inferred the wrong type | 9 | confirmed; largely the same defect as §1. |
| f-string: nested format spec | 8 | **confirmed, verified** (§2c) — the spec text is emitted literally. |
| boxing: value resolves to `0`/`None` | 7 | **confirmed and explained.** The value *is* boxed; the `any` method-dispatch chain has no `str` arm, so every str method returns `0`. |
| closures / callable values not modelled | 7 | confirmed. |
| `round(x, n)` returns float | 5 | **confirmed, split** into binary scaling (§2d) and negative-places return type (§2e). |
| stdlib bindings: function missing | 5 | confirmed. |
| stdlib module has no bindings | 5 | confirmed — `marshal`, `reprlib`, `unicodedata` ship no bundled source. |
| **finally does not run on return** | 4 | **FIXED.** All five such cases have left the failing set (`f4474ede`). |
| arbitrary-precision int absent | 3 | confirmed — `2**63` wraps, `bignum_power` → `0`. |
| dynamic class creation: 3-arg `type()` | 3 | **confirmed, verified** — and it is why the `namedtuple` shim cannot build its class. |
| parser: syntax not supported | 3 | **understated.** The frontend gap is larger, and much of it is fixed on the unmerged branch. |
| f-string: percent format spec | 2 | **confirmed, verified** (§2b). |
| sort/sorted ignores `__lt__` | 2 | **confirmed, verified** — `sorted([P(3),P(1),P(2)])` returns input order. |
| `__getattr__` unsupported | 1 | confirmed (`class_getattr_dynamic` → `0`). |
| closure captures the wrong value | 1 | confirmed. |
| exception `__str__` ignored | 1 | confirmed (`exc_custom_str` prints nothing). |
| mutable default argument rejected | 1 | **mislabelled.** The case is `syntax_semicolons.py`, and at head it fails with `[L002] unexpected character ';'` — the lexer does not know the character. Nothing to do with default arguments. Verified to **pass at `bb54e509`**. |

### Three things the previous audit could not see

1. **65 run-time crashes**, 55 of them access violations. It had no crash
   category; `PHASE0.md` named three and called them the highest-severity items
   in the corpus. They are 20% of all failures.
2. **98 compile refusals** rather than 31, because the measurement did not
   disable the `pyinbin` fallback.
3. **One `sorted([(1,2),(0,9),(1,1)])`** returns `[(0,9),(1,2),(1,1)]` — tuple
   comparison stops at element 0. That single defect explains
   `sorted_tuples_multi`, which the previous audit listed as NOT root-caused.

### A methodological note that affects every count

Diagnostic-code totals overstate the number of distinct defects. **14 of the 16
cases reporting `[E001]` report it alongside another diagnostic**: a refused call
leaves its target name unbound, so every later use of that name raises a second,
dependent error. Only `53_dynamic_import.py` and `closure_over_multiple.py`
report `[E001]` on its own. Counts here are by *cause*, not by code.

---

## How this audit was produced, and how to redo it

Everything here was measured. Where a claim could not be measured it is labelled
**NOT root-caused** rather than reasoned to.

### The instrument

```bash
ASMPYTHON_EMIT_IR=out.ir python -m asmpython <case> --target windows \
    -o out.exe --no-pyinbin-fallback
```

`--no-pyinbin-fallback` is not optional. The fallback is **on by default**
(`_compiler/__main__.py:1258`): when the native backend refuses a program, the
CLI runs it through the `pyinbin` interpreter instead, prints its output, and
exits 0. Without the flag, a program the compiler cannot compile looks like it
works.

It also distorts the corpus's own reporting. With the fallback on, a refusal
produces exit 0 and **no artifact**, so `tests/runner.py` then fails trying to
execute a file that was never written, and `_safe_run` reports it as:

```text
  [FAIL] compat_class_value_tuple.py
        runner error: [WinError 2] The system cannot find the file specified
```

The checked-in `results.txt` contains **56** of those, plus **42** that report
`compile failed:` outright — 98 in total, matching the number of native compile
refusals this sweep found, from the other direction. `results.txt` was written at
a nearby but not identical tree state (821/1144 against this audit's 813/1143),
so read that agreement as corroboration rather than as a second exact count.

> **Recommendation.** `tests/runner.py` should pass `--no-pyinbin-fallback` by
> default. It costs nothing (the corpus already cannot pass a case whose
> artifact is missing) and converts 56 generic `runner error` lines into real
> diagnostics.

### Why instrumenting rather than reading

`PHASE1.md` records three separate causes reasoned out from compiler source that
were all wrong, each settled in minutes by one IR dump. That pattern held here.
Two hypotheses formed while writing this audit were killed by their own probes:

- *"Containers lose element kinds."* **False.** All 18 container sites — bare
  list, `list[str]`, `list[object]`, literals, heterogeneous, iteration, `pop`,
  slice, nested, dict, tuple, set — round-trip a `str` correctly. The defect is
  at parameter boundaries, not in containers.
- *"A `const 0` return is the graceful-stub signature."* **Not usable as an
  aggregate.** It matches all 233 IR dumps, because every `main` ends
  `ret const 0` and every `__init__` returns `None` the same way. Reported here
  only so nobody re-derives it as a metric.

One measurement error of mine is worth recording for the same reason: the
`pointerish` flag is a heuristic (a 6+ digit run in stdout absent from the
expectation) and it false-positives on `bignum_factorial.py`, where the big
number is genuine integer overflow. It is corrected in the appendix.

### Artifacts

| file | what it holds |
|---|---|
| `triage.jsonl` | one record per failing case: status, diagnostic codes, exit code, first diverging line, IR signal counts |
| `_ir/<case>.ir` | the IR dump the backend consumed, for all 233 cases that reached lowering |
| `probe.py` | single-case harness: `python probe.py <case>` or `--code "..."`, with `--ir` / `--grep` |
| `matrix*.py` | the write-site, inference-boundary, use-site and formatting conformance matrices |
| `attribute.py` | static `ast` screen for the inference-boundary shape |

The sweep is resumable and was run **2-wide on a 4-core box** so a concurrent
agent kept usable cores; it is not a `tests.runner` sweep and must not be
replaced by one.

### Reproducing a single claim

```bash
cd <worktree>
python probe.py --code "def f(s):
    print(s)
x = 'abc'
f(x)"
# COMPILE: ok / GOT: 5368737797 / IR-SIGNALS: _abi_new_box=0 ...
```

---

## Appendix: every failing case and what was measured

Facts from the sweep, grouped by observable failure mode. No
attribution -- see the cause sections above for that. `IR` columns are
counts from the dump the backend consumed.

Cases marked **[origin]** (38 of 330) pass at
`origin/beta/3.14.0` (`bb54e509`) and are recovered by merging that
branch rather than by any fix described here. All 330 were measured
there, so that set is exact.


### Passes at the origin tip -- unmerged branch work (cause 0) — 34

All 34 built and run at `bb54e509`: **34/34 PASS**.

| case | how it fails on the local branch |
|---|---|
| `algo_dfs_recursive.py` **[origin]** | OUTPUT-DIFF: got '[]' |
| `algo_prime_sieve.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P002] expected NEWLINE, got OP '=' \|       is_prime[0]  |
| `app_template_render.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `ascii_builtin.py` **[origin]** | COMPILE-FAIL: asmpython: 'ascii' |
| `callable_as_default_arg.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P001] unexpected token KEYWORD 'lambda' \|   def process |
| `compose_functions.py` **[origin]** | COMPILE-FAIL: NameError: [E002] undefined function 'f' |
| `conversion_int_index_context.py` **[origin]** | RUN-CRASH: 0x1 |
| `default_arg_expression.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P002] expected OP ',', got OP '+' \|   def f(x, n=2 + 3) |
| `dunder_eq_hash_dict_key.py` **[origin]** | RUN-CRASH: 0x1 |
| `dunder_index.py` **[origin]** | RUN-CRASH: 0x1 |
| `find_duplicates.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `function_returning_function.py` **[origin]** | COMPILE-FAIL: AttributeError: [E113] 'any' is not callable (only a function, lambda, |
| `generator_in_join.py` **[origin]** | COMPILE-FAIL: TypeError: [E022] str.join() requires list[str], got list[int] |
| `indented_tree.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `int_prog_validator.py` **[origin]** | OUTPUT-DIFF: got "['name required']" |
| `list_comprehension_two_cond.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P002] expected OP ']', got KEYWORD 'if' \|   print([x fo |
| `list_index_of_tuple.py` **[origin]** | RUN-CRASH: 0x1 |
| `literal_float_forms.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P001] unexpected token OP '.' \|   print(.5, 5., 1.5) \| |
| `matmul_operator.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P002] expected OP ')', got OP '@' \|   print((Mat(2) @ M |
| `multiple_assignment_targets.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P002] expected NEWLINE, got OP '=' \|   d['a'] = d['b']  |
| `nested_tuple_unpack.py` **[origin]** | COMPILE-FAIL: SyntaxError: [E115] tuple assign with subscript/attribute targets requ |
| `nested_unpacking_for.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P026] nested unpacking in a loop target is not supported |
| `ospath_basename.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `ospath_dirname.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `ospath_join.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `parenthesized_context_managers.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P002] expected OP ')', got KEYWORD 'as' \|   with (M('a' |
| `prog_filter_chain.py` **[origin]** | COMPILE-FAIL: SyntaxError: [P002] expected OP ']', got KEYWORD 'if' \|   result = [x |
| `r39_dict_invert_multi.py` **[origin]** | COMPILE-FAIL: TypeError: [E022] dict.setdefault() key must be a str |
| `r39_flatten_dict.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `r39_url_builder.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `r40_gradient.py` **[origin]** | RUN-CRASH: 0xc0000005 |
| `set_symmetric_difference.py` **[origin]** | COMPILE-FAIL: TypeError: [E013] unsupported operand type for ^: set |
| `sim_grade_report.py` **[origin]** | OUTPUT-DIFF: got 'alice: 30.0' |
| `sim_matrix_stats.py` **[origin]** | RUN-CRASH: 0xc0000005 |

### Compile refused, no diagnostic code (parser / unimplemented lowering / internal) — 19

| case | codes | compiler said |
|---|---|---|
| `479_dynamic_classvar_reads.py` | — | asmpython: undefined symbol 'OverrideType__get_value' has no known DLL |
| `75_assembly_func.py` | — | asmpython: this program uses '@assembly_func' (raw inline NASM), which |
| `bytes_decode.py` | — | asmpython: unsupported expr MethodCall (list.decode) |
| `complex_literal.py` | — | SyntaxError: [P002] expected NEWLINE, got NAME 'j' \|   z = 2 + 3j \|  |
| `del_slice.py` | — | asmpython: unsupported expr Slice |
| `dict_dict_comprehension.py` | — | SyntaxError: [P002] expected OP '}', got KEYWORD 'for' \|   matrix = { |
| `extended_slice_assign.py` | — | asmpython: unsupported stmt IndexAssign (slice step) |
| `fstring_equals_debug.py` | — | parse error: unexpected tokens in f-string expression: 'x=' \|   print |
| `generator_send_skip.py` | — | SyntaxError: [P001] unexpected token KEYWORD 'yield' \|           x =  |
| `lambda_nested.py` | — | SyntaxError: [P001] unexpected token KEYWORD 'lambda' \|   add = lambd |
| `lib_contextlib_closing.py` | — | asmpython: unsupported expr MethodCall (str.close) |
| `lib_re_compile.py` | — | asmpython: unsupported expr MethodCall (str.findall) |
| `map_method_ref.py` | — | asmpython: unsupported expr Call (map() with a non-lambda predicate) |
| `property_deleter.py` | — | asmpython: unsupported stmt Del (Attr) |
| `set_update_multiple.py` | — | asmpython: unsupported expr MethodCall (set.update) |
| `slice_assignment_step.py` | — | asmpython: unsupported stmt IndexAssign (slice step) |
| `str_format_map.py` | — | asmpython: unsupported expr MethodCall (str.format_map) |
| `str_template_manual.py` | — | asmpython: unsupported expr MethodCall (str.format) |
| `syntax_semicolons.py` **[origin]** | — | SyntaxError: [L002] unexpected character ';' \|   a = 1; b = 2; c = 3  |

### Compile refused: argument arity or type — 19

| case | codes | compiler said |
|---|---|---|
| `296_collections_namedtuple.py` | [E021] | TypeError: [E021] type() takes 1 argument(s), got 3 |
| `double_star_merge_call.py` | [E021] | TypeError: [E021] call takes at most one **expr argument |
| `enum_functional.py` | [E001] [E021] | TypeError: [E021] Enum() takes 1 argument(s), got 2 |
| `generator_class_iterator.py` | [E022] | TypeError: [E022] list() argument Countdown is not iterable (needs __n |
| `lib_collections_counter_subtract.py` | [E001] [E021] | TypeError: [E021] Counter() got an unexpected keyword argument 'a' |
| `lib_collections_defaultdict.py` | [E022] | TypeError: [E022] dict() requires a dict or list-of-pairs argument |
| `lib_collections_namedtuple.py` | [E021] | TypeError: [E021] type() takes 1 argument(s), got 3 |
| `lib_collections_namedtuple_methods.py` | [E021] | TypeError: [E021] type() takes 1 argument(s), got 3 |
| `lib_contextlib_suppress.py` | [E021] | TypeError: [E021] suppress() takes 0 argument(s), got 1 |
| `lib_csv_dictreader.py` | [E001] [E022] | TypeError: [E022] list() argument DictReader is not iterable (needs __ |
| `lib_functools_cmp.py` | [E135] | TypeError: [E135] key= must be a lambda literal, a name bound to a lam |
| `lib_hashlib_md5.py` | [E021] | TypeError: [E021] md5() takes 0 argument(s), got 1 |
| `lib_hashlib_sha256.py` | [E021] | TypeError: [E021] sha256() takes 0 argument(s), got 1 |
| `lib_itertools_product_repeat.py` | [E021] | TypeError: [E021] product() got an unexpected keyword argument 'repeat |
| `lib_random_randrange.py` | [E021] | TypeError: [E021] random.randrange() takes 1 argument(s), got 2 |
| `lib_string_template.py` | [E021] | TypeError: [E021] substitute() got an unexpected keyword argument 'nam |
| `lib_types_simplenamespace.py` | [E001] [E021] | TypeError: [E021] SimpleNamespace() got an unexpected keyword argument |
| `lib_uuid_int.py` | [E001] [E021] | TypeError: [E021] UUID() got an unexpected keyword argument 'int' |
| `namedtuple_unpacking.py` | [E021] | TypeError: [E021] type() takes 1 argument(s), got 3 |

### Compile refused: no such method / attribute — 14

| case | codes | compiler said |
|---|---|---|
| `211_argparse_module.py` | [E012] [E113] | TypeError: [E012] unsupported operand type for +: str + int |
| `algo_count_islands.py` | [E113] | AttributeError: [E113] int has no method 'add' |
| `compat_class_value_tuple.py` | [E113] | AttributeError: [E113] int has no method 'tag' |
| `complex_arithmetic_skip.py` | [E113] | AttributeError: [E113] int has no method '__add__' |
| `dispatch_table_class_methods.py` | [E113] | AttributeError: [E113] 'any' is not callable (only a function, lambda, |
| `lib_contextlib_manager.py` | [E119] | AttributeError: [E119] '_genobj_tag' object does not support the conte |
| `lib_math_dist.py` | [E120] | AttributeError: [E120] module 'math' has no callable 'dist' |
| `lib_math_prod.py` | [E120] | AttributeError: [E120] module 'math' has no callable 'prod' |
| `lib_random_gauss.py` | [E120] | AttributeError: [E120] module 'random' has no callable 'gauss' |
| `lib_re_sub_func.py` | [E113] | AttributeError: [E113] int has no method 'group' |
| `lib_time_strftime.py` | [E120] | AttributeError: [E120] module 'time' has no callable 'struct_time' |
| `lib_time_struct.py` | [E001] [E120] | AttributeError: [E120] module 'time' has no callable 'gmtime' |
| `nested_closures.py` | [E113] | AttributeError: [E113] 'any' is not callable (only a function, lambda, |
| `proj_event_dispatch.py` | [E113] | AttributeError: [E113] 'int' is not callable (only a function, lambda, |

### Compile refused: undefined name, function or module — 12

| case | codes | compiler said |
|---|---|---|
| `53_dynamic_import.py` | [E001] | NameError: [E001] undefined variable '__pyinbin_loader__' |
| `class_hash_in_set.py` | [E001] [E055] | TypeError: [E055] set elements of type instance:K are not supported ye |
| `closure_over_multiple.py` | [E001] | NameError: [E001] undefined variable 'x' |
| `complex_number_basic.py` | [E001] [E002] | NameError: [E002] undefined function 'complex' |
| `lib_csv_writer.py` | [E001] [E005] | ModuleNotFoundError: [E005] cannot call csv....writer(): no module 'cs |
| `lib_hmac_new.py` | [E001] [E005] | ModuleNotFoundError: [E005] cannot call hashlib....new(): no module 'h |
| `lib_marshal_roundtrip.py` | [E005] | ModuleNotFoundError: [E005] cannot call marshal....dumps(): no module  |
| `lib_reprlib_repr.py` | [E005] | ModuleNotFoundError: [E005] cannot call reprlib....repr(): no module ' |
| `lib_unicodedata_name.py` | [E005] | ModuleNotFoundError: [E005] cannot call unicodedata....category(): no  |
| `partial_application_manual.py` | [E002] | NameError: [E002] undefined function 'fn' |
| `proj_call_factory.py` | [E001] [E012] | TypeError: [E012] unsupported operand type for +: str + int |
| `zip_star_unpack.py` | [E001] [E023] | semantic error: [E023] *expr argument unpacking requires a tuple with  |

### Compile refused by the type checker — 17

| case | codes | compiler said |
|---|---|---|
| `dunder_getitem_slice.py` | [E017] | TypeError: [E017] slicing not supported on instance:Seq |
| `exc_type_error.py` | [E012] | TypeError: [E012] unsupported operand type for +: str + int |
| `except_multiple_types.py` | [E132] | TypeError: [E132] list element of type type is not supported yet |
| `lib_collections_userdict.py` | [E017] [E043] | TypeError: [E043] 'MyDict' object does not support index assignment |
| `lib_configparser.py` | [E017] | TypeError: [E017] 'ConfigParser' object does not support indexing |
| `lib_enum_iteration.py` | [E018] | TypeError: [E018] cannot iterate a type in a comprehension |
| `lib_functools_partial_kw.py` | [E012] | TypeError: [E012] unsupported operand type for +: int + str |
| `lib_gzip_roundtrip.py` | [E127] | TypeError: [E127] cannot compare str and list with '==' |
| `lib_json_nested.py` | [E016] | TypeError: [E016] string index must be an int |
| `multiple_decorators.py` | [E012] | TypeError: [E012] unsupported operand type for +: str + int |
| `r39_group_consecutive.py` | [E017] | TypeError: [E017] cannot index a ? |
| `sim_leaderboard.py` | [E116] | ValueError: [E116] for ... in enumerate(...) needs two targets ('for i |
| `str_format_nested_field.py` | [E152] | ValueError: [E152] str.format() attribute/index access in fields (e.g. |
| `string_percent_dict.py` | [E133] | ValueError: [E133] bad format string: unsupported format character '(' |
| `tuple_concat.py` | [E013] | TypeError: [E013] unsupported operand type for +: tuple |
| `tuple_repeat.py` | [E013] | TypeError: [E013] unsupported operand type for *: tuple |
| `vars_of_instance.py` | [E149] | RuntimeError: [E149] vars() is not supported: it requires a Python int |

### Access violation (0xC0000005) at run time — 45

| case | exit | IR: new_box / anyunbox | imports |
|---|---|---|---|
| `469_guarded_class_string.py` | 0xc0000005 | 0 / 0 | — |
| `470_static_class_registry.py` | 0xc0000005 | 0 / 120 | — |
| `476_data_descriptor_precedence.py` | 0xc0000005 | 0 / 50 | — |
| `algo_merge_sort.py` **[origin]** | 0xc0000005 | 4 / 150 | — |
| `app_expression_eval.py` | 0xc0000005 | 7 / 120 | — |
| `app_json_config.py` | 0xc0000005 | 5 / 10 | — |
| `bound_method_frozenset_contains.py` | 0xc0000005 | 0 / 160 | — |
| `compat_class_registry.py` | 0xc0000005 | 1 / 190 | — |
| `compat_metaclass_descriptor_collect.py` | 0xc0000005 | 0 / 10 | — |
| `compat_type_parameter_specialize.py` | 0xc0000005 | 2 / 380 | — |
| `complex_via_real.py` | 0xc0000005 | 0 / 90 | — |
| `conditional_import_fallback.py` | 0xc0000005 | 0 / 100 | collections |
| `custom_exception_attr.py` | 0xc0000005 | 0 / 112 | — |
| `decimal_precision.py` | 0xc0000005 | 0 / 0 | decimal |
| `decorator_preserve_result.py` | 0xc0000005 | 1 / 90 | — |
| `decorator_with_args.py` | 0xc0000005 | 1 / 90 | — |
| `docstring_module_access.py` | 0xc0000005 | 0 / 80 | — |
| `dunder_radd.py` | 0xc0000005 | 0 / 0 | — |
| `exc_args_tuple.py` | 0xc0000005 | 0 / 112 | — |
| `generator_pipeline.py` | 0xc0000005 | 1 / 10 | — |
| `iter_two_arg_sentinel.py` | 0xc0000005 | 1 / 40 | — |
| `lib_array_basic.py` | 0xc0000005 | 1 / 80 | array |
| `lib_array_typecodes.py` | 0xc0000005 | 1 / 100 | array |
| `lib_cmath_sqrt.py` | 0xc0000005 | 0 / 0 | cmath |
| `lib_collections_counter_total.py` | 0xc0000005 | 0 / 0 | collections |
| `lib_collections_counter_update.py` | 0xc0000005 | 0 / 0 | collections |
| `lib_collections_ordereddict_move.py` | 0xc0000005 | 0 / 90 | collections |
| `lib_csv_reader.py` | 0xc0000005 | 0 / 0 | csv |
| `lib_datetime_combine.py` | 0xc0000005 | 0 / 80 | datetime |
| `lib_enum_auto.py` | 0xc0000005 | 0 / 160 | enum |
| `lib_enum_basic.py` | 0xc0000005 | 0 / 160 | enum |
| `lib_graphlib_topo.py` | 0xc0000005 | 0 / 10 | graphlib |
| `lib_itertools_chain_from_iter.py` | 0xc0000005 | 0 / 0 | itertools |
| `lib_itertools_groupby.py` | 0xc0000005 | 0 / 0 | itertools |
| `lib_itertools_tee.py` | 0xc0000005 | 0 / 10 | itertools |
| `lib_operator_contains.py` | 0xc0000005 | 0 / 0 | operator |
| `lib_operator_methodcaller.py` | 0xc0000005 | 0 / 10 | operator |
| `lib_re_named_groups.py` | 0xc0000005 | 0 / 0 | re |
| `metaclass_basic.py` | 0xc0000005 | 0 / 80 | — |
| `nested_dict_default.py` | 0xc0000005 | 8 / 150 | collections |
| `ospath_splitext.py` | 0xc0000005 | 0 / 90 | os.path |
| `param_mixed_all_kinds.py` | 0xc0000005 | 0 / 10 | — |
| `path_join_manual.py` | 0xc0000005 | 4 / 80 | — |
| `returning_bound_method.py` | 0xc0000005 | 0 / 80 | — |
| `zip_longest_manual.py` | 0xc0000005 | 0 / 0 | — |

### Other run-time failure (uncaught exception / stack overflow) — 6

| case | exit | IR: new_box / anyunbox | imports |
|---|---|---|---|
| `475_dynamic_dict_index_assign.py` | 0x1 | 0 / 80 | — |
| `crash_recursive_float.py` | 0xc00000fd | 0 / 0 | — |
| `lib_json_parse_array.py` | 0x1 | 0 / 0 | json |
| `type_alias_annotation.py` | 0x1 | 0 / 10 | typing |
| `vm_dict_key_through_any.py` | 0x1 | 4 / 260 | — |
| `with_suppress_exception.py` | 0x1 | 0 / 0 | — |

### Wrong output: a raw heap pointer where a value was expected — 52

| case | want | got | ptr? |
|---|---|---|---|
| `app_matrix_rotate.py` | [[3, 1], [4, 2]] | [10822960, 10823104] | yes |
| `app_pagination.py` | 4 [0, 1, 2] [9] | 4 22488288 22488544 | yes |
| `app_validate_form.py` | [('age', 'too young'), ('email', 'required')] | [('age', 5368746013), ('email', 5368746000)] | yes |
| `bignum_factorial.py` | 15511210043330985984000000 | 7034535277573963776 |  |
| `bytes_from_str.py` | b'abc' | 13640560 | yes |
| `compat_analysis_dynamic_return.py` | value=x | value=5368737804 | yes |
| `compat_ordered_flow_combined.py` | leaf:b | 10561296 | yes |
| `conditional_function_selection.py` | 5 | 17376144 | yes |
| `crash_bool_int_float_mix.py` | 4.5 | 4612811918334230530 | yes |
| `crash_float_nested_container.py` | 1.5 | 4609434218613702656 | yes |
| `data_sort_by_key.py` | ['A', 'C'] | [5368746001, 5368746007] | yes |
| `deeply_nested_comprehension.py` | [[[0, 1], [1, 2]], [[1, 2], [2, 3]]] | [[13706448, 13706592], [13708912, 13708960]] | yes |
| `dunder_format.py` | 25C | 20980880 | yes |
| `float_percentage_func.py` | 25.0 | 5368736870 | yes |
| `format_binary_grouped.py` | 1111_1111 | 11111111 | yes |
| `format_spec_grouping.py` | 1,234,567 1111_1111 | 1,234,567 11111111 | yes |
| `function_with_side_effect_list.py` | ['a', 'b'] | [5368737792, 5368737794] | yes |
| `int_negative_shift.py` | -4 255 | 9223372036854775804 255 | yes |
| `int_prog_csv_aggregate.py` | [('a', 15), ('b', 20)] | [('a', 10737532940), ('b', 5368766471)] | yes |
| `int_prog_parser.py` | app 1.0 | 15148544 15148848 | yes |
| `int_prog_priority_queue.py` | a b | 5368762410 5368762412 | yes |
| `int_prog_todo.py` | ['b'] | [5368746038] | yes |
| `int_prog_tokenizer.py` | [12, '+', 34, '*', 5] | [12, 9905200, 34, 9905376, 5] | yes |
| `lambda_default_arg.py` | 15 25 | 5368758989 25 | yes |
| `lib_copy_deepcopy.py` | [[1, 2], [3, 4]] [[9, 2], [3, 4]] | [[22423008, 2], [3, 4]] [22422752, 22422832] | yes |
| `lib_functools_reduce_initial.py` | 100 | 24520480 | yes |
| `lib_functools_reduce_strings.py` | abc | 21475000864 | yes |
| `lib_itertools_accumulate_func.py` | [1, 2, 6, 24] | [5368713968, 5368713969, 5368713971, 5368713974, 5368713978] | yes |
| `lib_itertools_combinations.py` | [(1, 2), (1, 3), (2, 3)] | [8004720, 8004816, 8004960] | yes |
| `lib_itertools_compress.py` | ['a', 'c'] | [4924352, 4924416] | yes |
| `lib_itertools_pairwise.py` | [(1, 2), (2, 3), (3, 4)] | [7414816, 7414896, 7415024] | yes |
| `lib_itertools_permutations.py` | [(1, 2), (1, 3), (2, 1), (2, 3), (3, 1), (3, 2)] | [16327888, 16327984, 16328128, 16328224, 16328320, 16328080] | yes |
| `lib_itertools_product.py` | [(1, 'a'), (1, 'b'), (2, 'a'), (2, 'b')] | [[1, 5368742071], [1, 5368742073], [2, 5368742071], [2, 5368742073]] | yes |
| `lib_itertools_repeat.py` | ['x', 'x', 'x'] | [5368741930, 5368741930, 5368741930] | yes |
| `lib_itertools_zip_longest.py` | [(1, 'a'), (2, '?'), (3, '?')] | [[1, 5368741963], [2, 21505040], [3, 21505040]] | yes |
| `lib_mimetypes.py` | text/html | 5368754374 | yes |
| `match_class_pattern.py` | o 3,4 | 5368741902 8989056 | yes |
| `match_guard.py` | neg zero pos | 5368737792 5368737796 5368737801 | yes |
| `match_literal.py` | one two other | 5368737792 5368737796 5368737800 | yes |
| `match_or_pattern.py` | small big | 5368737792 5368737798 | yes |
| `match_sequence.py` | origin xaxis point | 5368746018 5368746025 5368746031 | yes |
| `mixed_int_float_list_sum.py` | 6.5 | 4612811918334230532 | yes |
| `r39_priority_sort.py` | ['b', 'a', 'c'] | [5368746007, 5368746005, 5368746009] | yes |
| `r40_mean_variance.py` | 5.0 5.0 | 4617315517961601024 4617315517961601024 | yes |
| `reduce_with_named_function.py` | 15 | 5974048 | yes |
| `repr_nested_dict.py` | {'a': {'b': {'c': 1}}} | {'a': {'b': 9250224}} | yes |
| `sim_text_adventure.py` | ended at: end | ended at: 5368750103 | yes |
| `sorted_multiple_criteria.py` | ['alice', 'carol', 'bob'] | [5368746018, 5368746024, 5368746028] | yes |
| `str_encode_errors.py` | b'abc' | 21832560 | yes |
| `vm_str_field_via_helper.py` | b | 5368754216 | yes |
| `vm_tuple_through_any.py` | a | 5368762368 | yes |
| `zip_and_dict.py` | alice 30 | 22160400 22160448 | yes |

### Wrong output, case imports a stdlib module — 24

| case | want | got | ptr? |
|---|---|---|---|
| `474_boolop_value_flow.py` | 1 | False |  |
| `class_comparison_total.py` | True True 1 | True False 1 |  |
| `conditional_import_pattern.py` | False | True |  |
| `lib_base64_encode.py` | b'aGVsbG8=' | [97, 71, 86, 115, 98, 71, 56, 61] |  |
| `lib_binascii_hexlify.py` | b'4142' | [52, 49, 52, 50] |  |
| `lib_calendar_isleap.py` | True False | 1 0 |  |
| `lib_calendar_monthrange.py` | (5, 29) | [5, 29] |  |
| `lib_difflib_close.py` | ['apply', 'apple', 'ape'] | ['apply', 'ape', 'apple'] |  |
| `lib_fnmatch.py` | True False | 1 0 |  |
| `lib_fractions_from_float.py` | 1/2 | 1 |  |
| `lib_glob_pattern_match.py` | ['a.py', 'c.py'] | [] |  |
| `lib_hashlib_update.py` | aaf4c61d | 2cf24dba |  |
| `lib_heapq_nlargest.py` | [8, 5, 3] | [5, 8, 2] |  |
| `lib_itertools_cycle.py` | [1, 2, 1, 2, 1] | [1, 2, 1, 2] |  |
| `lib_json_roundtrip.py` | [1, 2, 3] | ['1', '2', '3'] |  |
| `lib_numbers_check.py` | True True | False False |  |
| `lib_operator_truth.py` | False True | 0 1 |  |
| `lib_pickle_roundtrip.py` | True | False |  |
| `lib_random_sample.py` | [0, 6, 9] | [6, 8, 9] |  |
| `lib_random_seeded.py` | 82 | 76 |  |
| `lib_random_shuffle.py` | [3, 4, 5, 1, 2] | [3, 1, 5, 4, 2] |  |
| `lib_re_groups.py` | user host | user@host user@host |  |
| `lib_statistics_median.py` | 3 | 3.0 |  |
| `lib_zlib_crc32.py` | True | False |  |

### Wrong output, core language (no import, no pointer) — 86

| case | want | got | ptr? |
|---|---|---|---|
| `464_metaclass_keyword.py` | Valid Python class-header keyword regression. | <missing> |  |
| `468_provider_type_runtime.py` | 1 | True |  |
| `468_static_data_descriptor.py` | 7 | 0 |  |
| `470_global_property_return.py` | 1 | True |  |
| `473_chained_property_method.py` | 1 | True |  |
| `app_dependency_resolve.py` | ['d', 'b', 'c', 'a'] | [] |  |
| `bignum_power.py` | 1267650600228229401496703205376 | 0 |  |
| `boolean_expression_complex.py` | True | 1 |  |
| `bytearray_mutate.py` | bytearray(b'xbc') | [120, 98, 99] |  |
| `callback_registry.py` | ['h1', 'h2'] | [0, 0] |  |
| `class_getattr_dynamic.py` | dyn_foo | 0 |  |
| `class_lt_sort.py` | [1, 2, 3] | [3, 1, 2] |  |
| `closure_default_arg_capture.py` | [0, 1, 2] | [0, 0, 0] |  |
| `compat_class_string.py` | Alpha | (null) |  |
| `compat_dynamic_parameter.py` | HI | 0 |  |
| `compat_iterable_element_helper.py` | ADA | 0 |  |
| `crash_conditional_type_change.py` | 1.5 | 3 |  |
| `crash_float_comparison_sort_key.py` | b | a |  |
| `crash_float_default_param.py` **[origin]** | 3.0 6.0 | 3.861206e-317 7.9094455e-317 |  |
| `crash_float_format_edge.py` | 1,234.57 1.234568e+03 1234.57 | 1,234.57 1.234568e+003 1234.57 |  |
| `crash_float_modulo_negative.py` **[origin]** | 0.5 -0.5 | -1.5 1.5 |  |
| `crash_nested_function_float.py` | 6.0 | 2.23505654e-316 |  |
| `default_arg_evaluated_once.py` | [1] | [] |  |
| `default_none_or_list.py` | [1] [2] | [] [] |  |
| `dunder_iadd.py` | 8 | 0 |  |
| `exc_custom_hierarchy.py` | caught as base SubErr | caught as base str |  |
| `exc_custom_str.py` | custom message |  |  |
| `except_hierarchy.py` | caught ValueError | caught str |  |
| `filter_returns_iterator.py` | 3 | 0 |  |
| `float_func_return.py` | 212.0 | 1 |  |
| `float_nan_compare.py` | False True | True False |  |
| `float_rounding_modes.py` | 2.67 | 2.68 |  |
| `float_scientific_upper.py` | 1.23E+04 | 1.23E+004 |  |
| `format_align_equals.py` | -     42 +     42 |      -42      +42 |  |
| `format_general_g.py` | 1.234e-05 1.234e+06 | 1.234e-005 1.234e+006 |  |
| `format_spec_percent.py` | 12.3% | 0.1234 |  |
| `fstring_exp.py` | 1.23e+04 | 1.23e+004 |  |
| `fstring_nested_fstring.py` |     3.14 | {w}.2f |  |
| `fstring_nested_spec.py` | 3.14 | {w - 3}f |  |
| `fstring_percent.py` | 25% | 0.25 |  |
| `fstring_percent_format.py` | 25.0% | 0.25 |  |
| `generator_expr_direct.py` | 0 1 | 0 0 |  |
| `init_subclass_hook.py` | ['A', 'B'] | [] |  |
| `int_bignum_pow.py` | 18446744073709551616 | 0 |  |
| `int_from_bytes.py` | 1024 | 0 |  |
| `int_to_bytes.py` | b'\x04\x00' | [4, 0] |  |
| `lambda_capturing_outer_var.py` | [30, 30, 30] | [0, 0, 0] |  |
| `lib_string_formatter.py` |     x\| | x}\| |  |
| `list_comp_with_walrus_call.py` | [0, 1, 4] | [0, 0, 0, 0] |  |
| `list_slice_assign_resize.py` | [1, 9, 4] | [1, 9, 3, 4] |  |
| `list_slice_assignment_grow.py` | [1, 10, 20, 2, 3] | [1, 2, 3] |  |
| `min_mixed_int_float.py` | 1.5 | 1.5e-323 |  |
| `multiple_context_vars.py` | [('a', 1), ('b', 2), ('c', 3)] | [] |  |
| `nested_dict_comp.py` | {0: {0: 0, 1: 0}, 1: {0: 0, 1: 1}} | {0: {'0': 0, '1': 0}, 1: {'0': 0, '1': 1}} |  |
| `number_sign_function.py` | 1 -1 0 | True True False |  |
| `operator_overload_comparison.py` | True False | True True |  |
| `pow_three_arg.py` | 24 | 0 |  |
| `prog_price_formatter.py` | ['$9.99', '$19.50', '$100.00'] | ['$9.99', '$19.5', '$100'] |  |
| `r39_char_histogram_sort.py` | i 4 | m 1 |  |
| `r40_compound_interest.py` | 1157.62 | 5.131006077194019e+18 |  |
| `r40_percentage_change.py` | 50.0 | 100.0 |  |
| `r40_series_sum.py` | 2.0833 | 24.0 |  |
| `repr_mixed_container.py` | {'list': [1, 2], 'tup': (3, 4)} | {'list': [1, 2], 'tup': [3, 4]} |  |
| `repr_none_in_list.py` | [1, None, 2, None] | [1, 0, 2, 0] |  |
| `repr_string_with_quotes.py` | ["it's", 'a "test"'] | ['it's', 'a "test"'] |  |
| `round_to_negative_places.py` | 12300 | 12300.0 |  |
| `sim_discount_calc.py` | [80.0, 40.0, 160.0] | [100.0, 100.0, 100.0] |  |
| `sorted_tuples_multi.py` | [(0, 9), (1, 1), (1, 2)] | [(0, 9), (1, 2), (1, 1)] |  |
| `sorted_with_none_handling.py` | [1, 2, 3] | [3, 1, 2] |  |
| `sorted_with_two_keys.py` | [('a', 1), ('a', 2), ('b', 2)] | [('b', 2), ('a', 2), ('a', 1)] |  |
| `str_splitlines_keepends.py` | ['a\n', 'b\n'] | ['a', 'b'] |  |
| `str_unicode_len.py` | 5 | 6 |  |
| `temperature_convert.py` | 212.0 32.0 98.6 | 1 1 1 |  |
| `type_name_lookup.py` | list | <missing> |  |
| `unicode_emoji_len.py` | 3 | 6 |  |
| `unicode_in_list_repr.py` | ['a', 'é', 'b'] | ['a', 'Ã©', 'b'] |  |
| `unicode_ord_high.py` | 20013 | 228 |  |
| `unicode_upper_accent.py` | É | Ã© |  |
| `vm_bool_is_not_int.py` | True | 1 |  |
| `vm_bytearray_mutable.py` | b'zbc' | [122, 98, 99] |  |
| `vm_bytes_literal.py` | b'abc' | [97, 98, 99] |  |
| `vm_callable_param_result.py` | HI | 0 |  |
| `vm_container_heterogeneous.py` | None | 0 |  |
| `vm_int_is_arbitrary_precision.py` | 1180591620717411303424 | 0 |  |
| `vm_none_is_not_zero.py` | None | 0 |  |
| `vm_round_returns_int.py` | 2.5 | 2.6 |  |

### Measurement anomalies — 2

| case | want | got | ptr? |
|---|---|---|---|
| `462_json_dumps_options.py` |  |  |  |
| `slice_step_zero_error.py` |  |  |  |
