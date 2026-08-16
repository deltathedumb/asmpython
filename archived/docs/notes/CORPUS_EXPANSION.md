# Corpus expansion — five areas the instrument could not see

Phase 0 built the measurement instrument and pointed it at two things: the
patch layer (29 `compat_` cases) and the value model (30 `vm_` probes). It
measured a 36% hit rate on new probes, and that rate never decayed.

A hit rate that does not decay is a statement about the *corpus*, not the
compiler. It means probes are still landing on untested ground, so the number
of known failures is a function of how many probes have been written rather
than of how much is broken. Everything outside those two areas was unmeasured
— stdlib bindings alone owned ~55 known failures with zero probes.

This adds **511 probes** across the five unmeasured areas. Nothing here changes
the compiler.

---

## 1. What was added

| generator | prefix | probes | area |
|---|---|---:|---|
| `gen_std_cases.py` | `std_` | 174 | stdlib binding contracts |
| `gen_obj_cases.py` | `obj_` | 94 | inheritance, MRO, descriptors, `__slots__`, metaclasses |
| `gen_flow_cases.py` | `flow_` | 90 | `with`, generators, closures, exceptions, `async`, `match` |
| `gen_proto_cases.py` | `proto_` | 81 | operator and iteration protocols |
| `gen_fmt_cases.py` | `fmt_` | 72 | format specs, f-strings, `%`-interpolation |
| | | **511** | |

Corpus: **1117 → 1628 cases.**

Every `# expect:` block is produced by executing the case under the host
CPython 3.14.6. None is hand-written — that is the rule `gen_vm_cases.py`
established and the reason a wrong expectation cannot get enshrined.

### The shared emitter adds two rules the corpus did not have

`tests/generators/_emit.py` holds the emission logic the two Phase 0
generators each carry a copy of, plus:

**Every case runs twice, in separate processes, and is refused if the two runs
disagree.** `PYTHONHASHSEED` is randomized per process, so a probe that prints
a `set` of strings — or anything derived from `hash()` — has a *different
correct answer* on every run. Recording one of them pins the corpus to a fluke
that no correct implementation can ever match, and the resulting case fails
forever while looking like a real requirement.

**A case whose body begins with a comment is refused.** `_parse_expect` reads
`#` lines until the first line that is not one, so a leading comment in the
body is absorbed into the expected stdout. This is the trailing-marker trap
from the other side.

### What the determinism check does not catch

One wave-2 probe still had to be caught by reading it. `sched.enter(0, prio,
...)` called twice looks order-deterministic and is not: `enter()` resolves its
delay against `monotonic()` at call time, so the earlier *call* wins regardless
of priority. Both generation runs agreed, so the double-run check passed it. It
now uses `enterabs()` against a frozen time function.

The double-run check catches per-process randomness. It cannot catch a timing
race. That gap is worth knowing about before trusting it.

---

## 2. Results

Measured in a dedicated worktree pinned to commit `00955558`, so
`asmpython/_runtime/_build` was never shared with the main tree.

| prefix | probes | pass | fail | hit rate |
|---|---:|---:|---:|---:|
| `std_` | 174 | 66 | 108 | **62.1%** |
| `obj_` | 94 | 35 | 59 | **62.8%** |
| `flow_` | 90 | 46 | 44 | **48.9%** |
| `proto_` | 81 | 38 | 43 | **53.1%** |
| `fmt_` | 72 | 45 | 27 | **37.5%** |
| **total** | **511** | **230** | **281** | **55.0%** |

**The rate went up, not down.** Wave 1 (340 probes) measured 46.5%. Wave 2 —
written specifically for what wave 1 had not touched — measured **72.1%**
(124/172). Newly-reached ground fails harder than ground already sampled,
which is the opposite of saturation.

### These numbers are stable, not phantom

The 339 wave-1 probes were run twice, in two independent full runs at the same
commit. **Zero verdicts changed.** That matters because the shared-`_build`
hazard has produced fake regressions in this repo before; a spread across runs
would have made every number here unusable.

---

## 3. The 281 failures, by symptom

| symptom | count |
|---|---:|
| wrong output | 122 |
| native refusal **masked by the pyinbin fallback** | 63 |
| compile refused (nonzero exit) | 43 |
| **crash** — access violation `0xC0000005` | 39 |
| runtime error (exit 1, no message) | 14 |

### 3.1 The masked-refusal channel is the structural finding

63 failures — 22% of all of them — never say what went wrong. The native
backend refuses the source, the CLI runs the program under pyinbin instead,
prints `pyinbin fallback executed successfully (no native artifact produced)`,
**exits 0**, and writes no binary. `tests.runner` then fails the case with:

```text
runner error: [WinError 2] The system cannot find the file specified
```

That is a true FAIL whose message names neither the case nor the cause.
Recompiling with `--no-pyinbin-fallback` recovers the real diagnostic every
time. PHASE0.md flagged this on exactly one case
(`compat_class_value_tuple`); it is a 63-case channel, and at the CLI every one
of them looks like a success.

Recovered diagnostics, most common first: `E120` module has no such callable
(10), `E021` wrong arity/unexpected keyword (9), `E001` undefined variable
cascading from an earlier refusal (9), `E113` no such method (5), `E022` not
iterable (5), `E017` indexing/slicing unsupported (5), `E013`/`E012`
unsupported operand (10), `E005` module has no bindings at all (4).

### 3.2 Crashes went from 3 to 42

Phase 0 noted that segfaults "were not previously represented at all" and added
three. These probes add **39 more**, all `0xC0000005`. They cluster on
class-object introspection and the dunder-dispatch paths:

```text
obj_mro_diamond_order          obj_classmethod_receives_class
obj_metaclass_new_adds_attr    obj_classmethod_inherited_cls
obj_data_descriptor_precedence obj_bound_method_self
obj_multiple_inheritance_attr  obj_class_of_instance
proto_iadd_mutates_in_place    proto_radd_enables_sum
proto_reversed_dunder          proto_custom_iter_feeds_builtins
flow_raise_from_sets_cause     flow_implicit_exception_context
flow_match_class_pattern       flow_decorator_preserves_metadata
std_ospath_split               std_sys_version_major
```

### 3.3 Themes in the 122 wrong-output failures

**A known type read back as a raw pointer — 25 cases.** The Phase 0 finding,
now reproduced well outside the value-model probes:

```text
flow_match_literal_patterns    want 'ok'              got '5368737792'
obj_diamond_cooperative_super  want ['D','B','C','A'] got [5368741892, 5368741890, ...]
flow_recursive_generator       want [1,2,3,4,5]       got [10063648, 10031840, ...]
```

**A value collapses to `0` or `1` — 21 cases.** `__getattr__`, `__setattr__`,
`__getattribute__`, `__new__`, metaclass `__call__`, `inspect.signature`,
`urlparse`, `uuid.int` and `2**100` all read back as `0`; `bool` results read
back as `1`.

**Whole subsystems return nothing.** `logging` emits an empty string through a
`StreamHandler`; a `threading.Thread` never appends to its list; the `atexit`
handler never runs.

**The `match` statement is uniformly broken — 7 of 7 probes fail.** It parses,
then produces raw pointers or the wrong branch. `async`/`await` fares better:
4 of 6 fail, but `asyncio.run` of a plain coroutine works.

**`bytes` is a list of ints.** `base64.b64encode(b"hello")` returns
`[97, 71, 86, ...]` rather than `b'aGVsbG8='` — the "bytes type absent" audit
item, now isolated.

**A caught exception loses its type.** `type(err).__name__` returns `str` for
both a user subclass and a builtin.

**Three-digit exponents — 5 cases.** `1.234568e+003` instead of
`1.234568e+03`, across `format()`, f-strings and `%`. The C runtime's exponent
width is reaching the output unmodified.

**Nested f-string spec fields emit their own source — 4 cases.**
`f"{3.14159:.{digits}f}"` prints `{digits}f`.

---

## 4. What is still uncovered

Inside the five areas:

- **37 of the 110 modules in `asmpython/stdlib/` still have no probe** (73 are
  now reached, up from 0):

```text
  concurrent_futures  fileinput   ftplib      gc          getpass
  glob                html_parser http_server imaplib     importlib
  linecache           locale      mimetypes   network     platform
  poplib              profile     pstats      quopri      shelve
  shutil              signal      smtplib     socket      socketserver
  sqlite3             ssl         subprocess  tarfile     tempfile
  timeit              token       tokenize    tracemalloc unittest
  urllib_error        uu
  ```

  Almost all of them need a filesystem, a clock, a socket or a subprocess, so
  covering them needs a fixture convention the single-file corpus does not have
  yet — not more probes of the same kind. The ones that could be probed today
  with no new machinery are `gc`, `linecache`, `token`, `tokenize`, `quopri`,
  `uu` and `html_parser`.
- **`match` is measured only at 7 probes** and fails all 7; the sub-patterns
  (nested class patterns, `as` bindings, value patterns) are unprobed.
- **Descriptor/`__set_name__` interaction with inheritance** is single-probed.

Outside them, and untouched by anything in this corpus:

- **Multi-module programs.** Every case is one file. `program_compat_fixes` is
  still the one patch module with no regression, for the same reason.
- **The C ABI / extension surface**, blocked on refcounting.
- **Memory behaviour** — aliasing, identity of large objects, cyclic structures.
- **Non-x86-64 backends.** Every number here is `--target windows` on x86-64;
  the ARM64 backend is unmeasured by this corpus.
- **Anything needing stdin, argv, environment or exit codes.**

---

## 5. Reproducing

```bash
git worktree add /c/Temp/corpuswt 00955558 --detach   # isolated _runtime/_build
cd /c/Temp/corpuswt
python -m tests.runner -j 8 -k std_          # or obj_ / flow_ / proto_ / fmt_
```

Never run two corpus sweeps against one tree: `asmpython/_runtime/_build` is
shared and both runs get phantom results.

To recover a diagnostic that the fallback swallowed:

```bash
python -m asmpython tests/cases/<case>.py --target windows \
    --no-pyinbin-fallback -o /c/Temp/out.exe
```

To regenerate any batch (expectations are re-derived from the host CPython):

```bash
python tests/generators/gen_std_cases.py tests/cases
```

---

# Round 2 — aliasing, fixtures, and the 63 re-classified

Measured in the same worktree, now pinned to `a99c0cff`.

Round 1's 511 probes were re-run first. **Zero verdicts changed** across
`aa81784b` (the unknown/int split, the boxed-return convention, and field-type
recovery through opaque receivers). The totals are identical to round 1's:
281 failing of 511, 55.0%.

## 1. The 63 masked refusals, grouped by cause

`a99c0cff` made this channel legible, so round 1's largest opaque bucket can
now be routed. One caveat on the fix: the runner surfaces the CLI's summary,
which says `native backend rejected this source: N semantic error(s)` — it
names the *count*, not the codes. Recovering the individual `[Exxx]` still
requires re-running with `--no-pyinbin-fallback`, so grouping the 63 needed a
second pass. Folding the per-error detail into that summary would remove it.

| cause | cases |
|---|---:|
| **C. a user-defined type is refused where a builtin is accepted** | **15** |
| **A. stdlib binding missing (a function, or the whole module)** | **13** |
| **E. language feature unimplemented** | **11** |
| D. operator unimplemented for a builtin container type | 9 |
| B. stdlib binding has the wrong signature | 6 |
| G. an unannotated value is typed `int` when it is not | 5 |
| F. parser: syntax not supported | 4 |

Three causes exceed 10 cases. A fourth appears if **A and B are read as one
workstream** — "the stdlib binding is missing or wrong" is **19 cases**, the
largest single routable group in the set.

**C (15)** is the most coherent. Everywhere a builtin type is accepted, a
user-defined one is refused: `list()`, `reversed()` and `zip()` reject an
object with `__iter__`; slicing and indexing are refused on an instance and on
a `list` subclass; `in` demands `__contains__` instead of falling back to
iteration; a set cannot hold instances. These are the documented protocol
fallbacks, missing as a group rather than one at a time.

**G (5)** is small but diagnostic: every case is `unsupported operand type for
+: str + int`, inside a decorator or a nested closure. The value is a `str`
that was typed `int` because nothing annotated it — the default-to-int rule
meeting first-class functions.

**F (4)** is worth separating because a parser refusal cannot be fixed in
sema: `yield` as an expression (`[P001]`), nested unpacking in a loop target
(`[P026]`), and the f-string `=` debug form (twice).

## 2. Aliasing — a confirmed silent miscompile

60 probes, 17 failing (28.3%). The headline is not the rate; it is *which* 17.

| sub-area | result |
|---|---|
| mutation through an alias, same scope | **9/9 pass** |
| identity across a call boundary | **5/5 pass** |
| mutation across a call boundary | 3/5 — **2 fail** |
| copy semantics | 7/10 |
| mutable default arguments | **0/3** |
| self-reference and cycles | **0/4** |
| `id()` and `is` | 7/9 |

Same-scope aliasing is correct, and identity *survives* a call:
`passthrough(a) is a` is `True`. But a list mutated through a **parameter**
does not reach the caller. Reduced to a standalone program and confirmed
outside the corpus:

```text
def f_setitem(xs): xs[0] = 99      CPython [99]     asmpython [8490768]
def f_append(xs):  xs.append(2)    CPython [1, 2]   asmpython [1]
def f_extend(xs):  xs.extend([3])  CPython [1, 3]   asmpython [1]
def f_dict_insert(d): d["new"] = 1 CPython len 2    asmpython len 2  (correct)
o.items.append(1) through a param  CPython [1]      asmpython [1]    (correct)
```

The buffer is shared — `xs[0] = 99` *does* reach the caller's list, writing a
raw pointer into it, which is a separate boxing bug. The **length** is not
shared: `append` and `extend` are lost. Dicts are unaffected, and a list
reached through an instance field is unaffected. That is the signature of a
list whose data pointer is passed by reference while its length lives in a
copied header.

This compiles cleanly, exits 0, and prints a plausible wrong answer. It is the
only failure class in this corpus that does not announce itself.

The inverse error is present in the same build: **`set(a)` does not copy.**

```text
a = {1, 2}; b = set(a); b.add(3)   CPython len(a) = 2   asmpython len(a) = 3
```

`copy.copy` and `copy.deepcopy` fail the same way. So values are copied where
they must be aliased *and* aliased where they must be copied.

The two `is` failures are the ones flagged in advance as implementation
details: `1 is True` returns `True` (bool is not distinguished from int), and
`1000 is int("1000")` returns `True` (no boxed identity for large ints).
Self-referential structures crash (`0xC0000005`) or print a raw pointer where
CPython prints `[...]`.

## 3. Fixtures — the convention works; the prerequisite does not

41 probes, 39 failing (95.1%). That is **not** 39 independent findings, and
the file is built so this can be said precisely: `fix_tempdir_write_read` owns
the shared prerequisite, and it is the one that fails. Reduced:

```text
h = open(p, "w"); h.write("payload"); h.close()
h = open(p); print(h.read())      CPython: payload      asmpython: 0
```

`read()` returns `0`. Every probe that reads a file back — `open`,
`linecache`, `fileinput`, `csv`, `json`, `configparser`, `shutil`, `os.stat`
— is downstream of that one defect, and the crashes among them are what
happens when `.rstrip()` or iteration is applied to the `0`. The convention
itself is sound: the probes build and remove their own fixtures, print nothing
environment-derived, and assert clocks as orderings. They begin reporting
independent facts as soon as file reads return their contents.

Three failures are genuinely separate: `os.rename`, `shutil.copy` and
`time.perf_counter` fail at *link* time — `undefined symbol 'rename' has no
known DLL`, likewise `feof` and `_time_perf_counter`. Those are missing
`native_libraries` declarations rather than compiler bugs.

## 4. Hit rate per wave

| wave | probes | failing | rate |
|---|---:|---:|---:|
| 1 — std/obj/flow/proto/fmt | 340 | 158 | 46.5% |
| 2 — the same areas, deeper | 172 | 124 | 72.1% |
| 3 — aliasing, fixtures, in-memory stdlib | 108 | 63 | 58.3% |
| **all** | **619** | **344** | **55.6%** |

Corpus: 1628 → **1736 cases**.

The rate has not bent. Wave 3 sits between waves 1 and 2, and its sub-areas
differ enormously — aliasing at 28% is the most conformant area measured so
far, fixtures at 95% the least. Averaging them hides that, which is the
argument for continuing to report per area rather than per wave.

## 5. Still uncovered after round 2

- **`uu` cannot be probed at all.** PEP 594 removed it from CPython in 3.13,
  so there is no reference implementation to derive an expectation from.
  `asmpython/stdlib/uu.py` ships a binding for a module the target language no
  longer has.
- **34 stdlib modules remain unprobed**, now for narrower reasons: network
  peers (`socket`, `ssl`, `smtplib`, `ftplib`, `imaplib`, `poplib`,
  `http_server`, `socketserver`, `urllib_error`), interactive or
  platform-specific surfaces (`getpass`, `locale`, `platform`, `signal`), and
  profiling (`profile`, `pstats`, `timeit`) whose output is inherently
  variable.
- **Concurrency semantics.** `threading` is probed only for "a joined thread
  ran"; nothing tests interleaving, locks under contention, or `queue` across
  real threads.
- **Multi-module programs**, the C ABI, and non-x86-64 backends, all unchanged
  from round 1.

---

# Round 3 — the container-element consumer matrix

Measured at `ae1dc7ae`, then re-run at `6112d342` — which includes `5c7e27a3`
("relabel, don't convert, a float already wearing an i64 label"). **The matrix
is byte-identical across the two.** That fix moved no cell in the consumer
matrix, including the twelve `float` failures, which says the consumer paths
are a separate axis from the value-level float handling rather than downstream
of it.

## 1. Re-measurement, and a correction to round 2

The 619 existing probes were re-run first. **6 fixed, 0 regressed.** All six
are agent 3's exact-decimal work landing exactly where predicted:
`fmt_spec_exponent`, `fmt_spec_e_uppercase`, `fmt_spec_g_uppercase`,
`fmt_spec_general_g`, `fmt_percent_exponent` and `fmt_percent_float_precision`.
The `fmt_` area fell from 37.5% to 29.2% failing.

**Round 2's aliasing diagnosis was wrong and is corrected here.** It said the
list's data pointer was shared while its length was not, and that dicts were
unaffected. Mapping the boundary refutes both halves:

```text
def m(xs): xs.sort()      CPython [1, 2, 3]   asmpython [3, 1, 2]   no effect
def m(xs): xs.reverse()   CPython [3, 2, 1]   asmpython [1, 2, 3]   no effect
def m(d):  d.clear()      CPython len 0       asmpython len 1       no effect
def s(d):  d["k"] = 9     CPython len 1       asmpython len 1       works
def m(xs): xs.append(2); return len(xs)
                          CPython callee 2, caller 2
                          asmpython callee 1, caller 1
```

`sort` and `reverse` do not change the length and still have no effect, which
kills the length explanation. Dicts are affected after all — `pop`, `clear` and
`update` all fail; only `d[k] = v` works. And the callee's **own** view does
not carry the append either, so nothing is being lost on the way back to the
caller.

The corrected statement: **a mutating method called on a parameter has no
effect at all**, while a subscript store through a parameter does take effect.
Not a length problem, and not list-specific.

Boundary, from `alias_param_*`:

| group | result |
|---|---|
| length-changing methods (append, extend, insert, pop, remove, clear, `dict.pop/clear/update`, `set.add/discard`) | **1/14 pass** |
| length-preserving methods (`sort`, `reverse`, `setitem`) | **0/4 pass** |
| subscript store `d[k] = v` through a parameter | passes |
| mutation via a parameter's *field* (`box.items.append`) | passes |
| mutation of a *global* without a parameter | passes |

The one passing length-changing case is `d[k] = v`, which is a subscript store
rather than a method call — consistent with the corrected reading.

## 2. The consumer matrix

134 probes: 30 consumer paths × 4 element kinds, plus 14 nested-container
cases. Rebuilt mechanically from case names and runner verdicts.

```text
consumer path           int   str float mixed
----------------------------------------------
comprehension_dict        .     .     .     X
comprehension_list        .     .     .     X
comprehension_nested      .     X     X     X
comprehension_set         .     .     .     X
dict_from_pairs           !     X     !     !
enumerate                 .     .     C     X
for_loop                  .     .     .     X
index_method              .     .     C     E
len                       .     .     .     .
membership                .     .     C     X
min_max                   .     .     .     X
min_max_key               .     .     C     C
pass_to_function          .     X     X     .
pop_method                .     .     .     X
remove_method             .     .     C     X
repr_container            .     .     .     X
repr_nested               .     .     .     X
set_ops                   .     .     C     X
slice                     .     .     .     X
slice_step                .     .     .     X
sort_inplace              .     .     .     X
sort_inplace_key          .     .     C     C
sorted                    .     .     .     X
sorted_key                .     .     C     C
starred_call              .     X     X     !
subscript                 .     .     .     X
subscript_computed        .     .     .     X
unpack                    .     .     .     .
unpack_in_for             .     .     .     X
zip                       .     .     .     X
----------------------------------------------
FAIL by kind              1     4    12    27

.  pass    X  wrong output    C  compile refused
!  crash   E  runtime error   R  masked refusal
```

120 cells, 44 failing (36.7%). Nested containers: 14 probes, 6 failing.

**Every one of the 30 paths is covered on every one of the 4 kinds.** There
are no blank cells, which is the property that makes this usable as a
migration gate rather than as a sample.

### What the matrix says

**Only two paths are correct on all four kinds: `len` and `unpack`.** Both
avoid reading an element's *value* — `len` reads the count, `unpack` binds a
fixed arity. Every path that actually consumes a value fails on at least one
kind.

**`mixed` fails on 27 of 30 paths**, and the dominant symptom is a single
defect: **`None` stored in a container reads back as `0`.**

```text
subscript           want 'None'                  got '0'
for_loop            want 'None'                  got '0'
zip                 want 'None None'             got '0 0'
slice               want '[True, None]'          got '[True, 0]'
repr_container      want "[1, 'two', 3.5, True, None]"
                    got "[1, 'two', 3.5, True, 0]"
```

That one fact accounts for most of the mixed column. It is the same
`vm_none_is_not_zero` property, now shown to reach fifteen separate consumers.

**Four paths silently return a value where CPython raises.** `min`, `max`,
`sorted` and `list.sort` on a heterogeneous list must raise `TypeError`;
instead they return an answer:

```text
sorted(mixed)    CPython TypeError    asmpython [0, 1, 'two', 3.5, True]
min(mixed)       CPython TypeError    asmpython 0
```

This is the highest-value class in the matrix — a wrong value in place of an
error, with no signal.

**`float` breaks more consumer paths than `str` (12 vs 4)**, and eight of the
twelve are compile refusals rather than wrong values: `enumerate`,
`index_method`, `membership`, `min_max_key`, `remove_method`, `set_ops`,
`sort_inplace_key`, `sorted_key`. Float elements are refused by the paths that
compare or key on them.

**`dict_from_pairs` fails on all four kinds** — the only path that does. Three
of the four crash outright; the `str` case writes raw pointers as the values
(`{'aa': 5368741891, ...}`). Building a dict from an iterable of pairs is
broken independent of element kind.

**`pass_to_function` and `starred_call` leak pointers for `str` and `float`
but pass for `int`**, which is the boxing boundary showing up at the call
edge rather than in the container.

**Nested containers** — corrected. An earlier draft of this paragraph said the
inner container's elements "survive a single level" and cited
`elem_nested_dict_of_lists_read` as passing. That was wrong: the case has
failed in every run it has ever been in. 6 of the 14 nested probes fail, and
the axis is **element kind, not nesting depth**:

```text
groups = {"x": [1, 2], "y": ["a"]}
groups["x"]      [1, 2]      correct
groups["x"][1]   2           correct
groups["y"][0]   'a' -> 5368762372     str leaks at depth 2
```

`int` elements survive nesting; `str` elements leak as soon as they are read
back out of one, at depth 2 — so "holds to depth two" is also wrong. Printing a
container that *holds* containers leaks as well: `([1, 2, 3], ['a'])` renders
as `(11936704, 11936784)`, even though indexing each element individually
returns it intact. `comprehension_nested` leaks for `str`, `float` and `mixed`
alike, being one of the paths that re-reads elements without carrying their
kind.

## 3. Hit rate per wave

| wave | probes | failing | rate |
|---|---:|---:|---:|
| 1 — std/obj/flow/proto/fmt | 340 | 158 | 46.5% |
| 2 — the same areas, deeper | 172 | 124 | 72.1% |
| 3 — aliasing, fixtures, in-memory stdlib | 108 | 63 | 58.3% |
| 4 — consumer matrix + parameter boundary | 166 | 71 | 42.8% |

Wave 4 splits sharply: the consumer matrix is 37.3% and the 32 new
parameter-boundary probes are 65.6%. Corpus: 1736 → **1903 cases**; probes
owned here: 619 → **785**.

The rate is drifting down but is nowhere near 10%, and the wave-4 figure is
the least meaningful of the four — a cross product deliberately includes the
combinations expected to pass, so its rate measures the shape of the product
rather than the density of defects.

## 4. Note for whoever owns `tests/runner.py`

`alias_param_setitem_str` fails as `runner error: 'NoneType' object has no
attribute 'replace'`. The compiled program writes bytes that are undecodable
in the console codepage, and the runner reports its own internal error instead
of the case's failure. Same class as the `WinError 2` masking fixed in
`a99c0cff`: a real FAIL arriving with a message about the harness. Decoding
the child's output with `errors="replace"` would make it report the actual
divergence.

## 5. Priority 3 status

The fixture convention was delivered in round 2 (`gen_fixture_cases.py`, 41
probes) and is unchanged. It remains gated on `open(p).read()` returning `0`.

## 6. Re-measured after the origin merge (`ad596be3`)

`2d441f9c` merged origin and brought ~1,500 lines of compiler change
(`sema.py` +599, `parser.py` +454, `ir_lower.py` +390, plus `codegen`,
`program`, `lexer` and both `abi_shims`). All 785 probes were re-run against
it.

**30 fixed, 1 regressed.**

### The round-2 silent miscompile is fixed

21 of the 30 fixes are `alias_param_*`. The original reduction now matches
CPython exactly:

```text
def f(xs): xs.append(2)      caller sees [1, 2]        (was [1])
def m(xs): xs.sort()         caller sees [1, 2, 3]     (was [3, 1, 2])
def m(xs): xs.reverse()      caller sees [3, 2, 1]     (was [1, 2, 3])
def m(d):  d.clear()         caller sees len 0         (was len 1)
callee's own view            len 2 / caller len 2      (was 1 / 1)
```

`alias_` went from 41.3% to **18.5%** failing — the largest single improvement
any wave has recorded. The corrected round-2 diagnosis (a mutating *method* on
a parameter has no effect; a subscript store does) is what the fix matched.

What is **not** fixed, and is the residue worth naming:

| still failing | symptom |
|---|---|
| `alias_mutation_through_object_annotation` | `def add(xs: object)` still loses the mutation — the fix covers unannotated parameters but not the `object`-annotated path |
| `alias_set_constructor_copies` | `set(a)` still aliases instead of copying |
| `copy.copy` / `copy.deepcopy` | exit 1 |
| mutable default arguments (3 probes) | the default is re-created per call |
| self-reference and cycles (4 probes) | crash or raw pointer |
| `alias_param_slice_assign` | `[7, 8, 2]` → `[7, 2]` |

So the copy-where-it-should-alias direction is fixed and the
alias-where-it-should-copy direction is not.

### The consumer matrix barely moved

One row changed: `pass_to_function` went from failing on `str` and `float` to
passing on all four kinds. **118 of 120 cells are unchanged.** Per-kind
failures went `1 / 4 / 12 / 27` → `1 / 3 / 11 / 27`; the `mixed` column did not
move at all, so the `None`-reads-back-as-`0` defect behind fifteen consumers is
untouched. A 1,500-line merge moving 2 of 120 cells is the argument for the
matrix: the consumer paths really are a separate axis from the value-level
work, and they will not fall out of it.

### One verified regression

`obj_lt_drives_sorted` was passing at `00955558` and at `ae1dc7ae`, and fails
at `ad596be3`:

```text
class Version:  __lt__ compares self.n
sorted([Version(3), Version(1), Version(2)])
    CPython    [v1, v2, v3]
    asmpython  [v3, v1, v2]       -- input order; __lt__ is not consulted
```

Reproduced twice under `-j 1` in an isolated worktree and once by compiling the
case directly, so it is not a phantom from the concurrent sweep that was
running at the time. `obj_lt_drives_min_max` fails too but was already failing,
so `sorted` is the part that moved. This is FAILURE_AUDIT rank 21
("sort/sorted ignores `__lt__` on instances") coming back.

### Rates after the merge

| prefix | probes | failing | rate |
|---|---:|---:|---:|
| `fix_` | 41 | 39 | 95.1% |
| `obj_` | 94 | 59 | 62.8% |
| `std_` | 181 | 113 | 62.4% |
| `proto_` | 81 | 40 | 49.4% |
| `flow_` | 90 | 44 | 48.9% |
| `elem_` | 134 | 47 | 35.1% |
| `fmt_` | 72 | 21 | 29.2% |
| `alias_` | 92 | 17 | 18.5% |
| **all** | **785** | **380** | **48.4%** |
