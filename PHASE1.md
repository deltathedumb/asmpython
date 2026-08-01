# Phase 1 — the value model

Phase 1's acceptance criteria are the failing `tests/cases/vm_*.py` probes from
`PHASE0.md`. This file records what was fixed, what the remaining probes are
actually blocked on, and how that changed the estimate.

---

## 1. A correction that changes the plan

The audit that produced this phase plan said the legacy NASM code generator
(`_compiler/codegen.py`) was the default backend and held most passing coverage.
**That is wrong.** `_compiler/__main__.py` resolves the backend per target:

```text
--target windows/linux        -> x86-64   (the IR backend: ir_lower.py + _backends/x86_64)
--target freestanding[16]     -> legacy   (codegen.py; being removed, --no-link replaces it)
```

`driver.py`'s `backend: str = "legacy"` is only the Python default of an
internal function, which the CLI always overrides. So the corpus, and every
ordinary build, runs on the **x86-64 IR backend**.

This matters because the two paths have completely different value models.

### The IR backend already has tagged values

The audit assumed Phase 1 meant building a runtime value representation from
nothing, on the basis of `codegen.py`'s design note ("All values are 64-bit
ints"). The IR backend does not work that way. `ir_lower._lower_box_any`
already allocates a real tagged cell:

```text
[BOX_MAGIC][tag][payload]      (_abi_new_box, 24 bytes)
```

with distinct tags for int / bool / float / str / list / dict / tuple / set,
a fault-safe reader (`_lower_read_any_tag`), a single documented write choke
point (`_lower_value_into_any_slot`), and a runtime tag-dispatching formatter
(`_lower_format_any_value`). Heterogeneous list literals already collapse to
`el_type = "any"` in sema and box their elements.

**So the foundation exists.** The failing probes are gaps in it, not its
absence — which makes Phase 1 substantially smaller than 3–4 months. Revised
estimate is in §4.

---

## 2. Fixed and verified

### `finally` did not run on any early exit path

`_lower_try` emits the `finally` body only under its two
`if not ctx.terminated:` fall-through guards. A `return`, `break`, or
`continue` inside the try terminates the block before reaching them, so the
finally was skipped entirely:

```python
def f():
    try:
        return "from-try"
    finally:
        print("finally-ran")     # never ran
```

Fixed by giving the lowering context a `try_finally_stack` index-aligned with
the existing `try_handler_stack`, plus `loop_try_depth` recording how deep the
try stack was when each loop was entered. A shared `_unwind_try_scopes(ctx,
down_to)` now leaves each scope innermost-first, restoring the parent handler
*then* running that level's finally — the order matters, so a raise inside a
finally propagates outward rather than back into its own try. `return` unwinds
to 0; `break`/`continue` unwind only to the enclosing loop's mark, since trys
around the whole loop are not being left.

**Result: corpus 801/1140 -> 809/1142. 6 fixed, 0 regressions**, confirmed by
`python -m tests.baseline --check -- -j 8` against the Phase 0 manifest.

| case | was |
|---|---|
| `exc_finally_return_override.py` | failing |
| `exc_finally_with_exception.py` | failing |
| `finally_on_return.py` | failing |
| `nested_try_finally.py` | failing |
| `exc_break_in_try.py` | failing |
| `vm_finally_runs_on_return.py` | failing (Phase 0 probe) |

`break`/`continue` were a gap in the first version of this fix, found by asking
what else leaves a try body; `vm_finally_runs_on_break.py` and
`vm_finally_runs_on_continue.py` were added to pin them, and fixing them is
what recovered `exc_break_in_try.py`.

### Two heap-corrupting buffer overflows

`_abi_float_fmt` and `_abi_int_fmt` in `_runtime/abi_shims.asm` both did:

```asm
mov rcx, 64
call malloc
...
call sprintf        ; unbounded write into a 64-byte block
```

`sprintf` does not bound its output. `print("%f" % 1e100)` needs 108 bytes and
terminated the process with `STATUS_HEAP_CORRUPTION` (0xC0000374); `%f` of
`DBL_MAX` needs 316; `"%500d" % 5` overflows the int shim the same way. This is
reachable from ordinary Python and is a memory-safety bug, not just a wrong
answer.

Both now size the allocation with a `_scprintf` probe — the msvcrt function
that returns the length a conversion *would* produce without writing it. C99
`snprintf(NULL, 0, ...)` would serve equally but **msvcrt.dll does not export
it** (only the pre-C99 `_snprintf`, which reports truncation as `-1`);
`_scprintf` was confirmed a real export via `ctypes.WinDLL('msvcrt.dll')`, the
method the rest of that table was built with. A negative probe result falls
back to a 1152-byte worst case, so a non-conforming libc over-allocates rather
than overflowing. `_scprintf` was added to `pe_linker._DLL_FOR_SYMBOL`.

Verified: `%f` of `1e100` → 108 chars, of `1e300` → 308 chars, both exit 0.

> Adjacent, NOT fixed: msvcrt's `printf` zero-fills past 17 significant digits,
> so those 308 characters are not CPython's exact decimal expansion. That is a
> separate accuracy bug in the same area — see §3's note on `round`.

### The shared runtime's `itoa_str_buf` was 32 bytes

`generate_runtime_only` in `codegen.py` builds the runtime object that the
**x86-64 backend links** (`driver._run_backend_x86_64` calls `build_runtime`),
and that runtime references `itoa_str_buf` in 8 places. It was reserved at 32
bytes with no call-site knowledge. It is now sized from the widest conversion
any site requests, with the prebuilt-runtime path reserving the worst case.

---

## 3. Not fixed — and it is all one defect

Every remaining probe reduces to a single fact about the type lattice:

> **`int` serves three jobs at once: the integer type, the unknown type, and
> the None sentinel.**

That is not an inference; the codebase names it:

- `boolop_value_compat_fixes.py`: "`int` doubles as the unknown/None sentinel"
- `sema.py`: "`int` as asmpython's generic unknown-sentinel"
- six sema helpers documented "'int' if unknown"
  (`_list_el_type`, `_dict_value_type`, `_resolve_field_el`, ...)
- `ast_nodes.is_none_expr`: None "parsed as IntLit(0) ... though its
  `expr_type` is 'int'"

The consequences are the probe list:

| conflation | probe(s) |
|---|---|
| unknown element/field/return type reads as `int`, so it is stored RAW instead of boxed and its kind is unrecoverable | `vm_str_field_via_helper`, `vm_tuple_through_any`, `vm_callable_param_result`, `vm_container_heterogeneous` |
| `None` reads as `int`, so it is indistinguishable from `0` | `vm_none_is_not_zero` |
| `bool` reads as `int`, so it renders `1`/`0` | `vm_bool_is_not_int` |

`ast_nodes.IntLit` is where it bottoms out: **four distinct Python values
share `IntLit(0)`**, told apart only by side-channel flags on the node.

```text
None      -> IntLit(0) + is_none
Ellipsis  -> IntLit(0) + is_ellipsis
False     -> IntLit(0) + is_bool
0         -> IntLit(0)
```

The node's own comments say why: the flags let `print`/`str`/f-strings render
"True"/"None"/"Ellipsis" "without a separate AST node or a distinct static
type", and `is_ellipsis` exists because `...` "needs its own marker flag
rather than reusing is_none, even though both share IntLit(0) as their
storage". A flag on a literal cannot survive being stored, returned, or
unified -- which is exactly where the probes fail.

The cost is visible in the consumer sites: the same three-way bool/None/int
dispatch is hand-written at **7 separate call sites** (`str()`, `repr()`,
`print()`, f-strings, `type()`, `isinstance()`, container formatting) as
adjacent `arg_t == "int" and A.is_bool_expr(...)` /
`... and A.is_none_expr(...)` pairs. Real types collapse each to one check.

This is why the runtime-side None fix in §2 had to fail: it tried to
disambiguate at the read site what the type system had already merged at the
write site. **No runtime-only fix exists for any of these.**

It also fixes the ordering. An earlier draft of this file had "give None a
type" first; that is wrong. `None` cannot be boxed distinctly until
genuinely-unknown values are boxed at all, because both currently claim to be
`int`. Splitting UNKNOWN out of `int` comes first and is what makes the
existing `_lower_box_any` machinery start applying.

### DONE: the unknown type is split out of `int`

> **Landed in `821ceb9c`: `UNKNOWN_TY = "any"`. Zero regressions, four cases
> fixed** -- `app_simple_orm`, `class_class_var_shared`, `dict_nested_mutate`,
> `set_comp_nested_iter`. Corpus 809/1143 -> 813/1143.
>
> The endpoint needed no new type. `"any"` already meant "kind not statically
> known, so box it and dispatch on the tag at read time"; the bug was only
> that the DEFAULT named the integer type instead. Landing it was one line,
> because the preceding commits had migrated every site that RETURNS the
> sentinel, and most sites that CONSUME it self-heal (`x if x != "int" else
> "any"` becomes the identity it always meant).
>
> It was measured, not assumed. A prediction was written first -- 30-80 newly
> failing cases, refined to 10-40 once the self-healing shape was noticed.
> The actual answer was **zero**. Being wrong by that margin is worth
> recording: the audit was the work, and the flip was the cheap part.
>
> Caveat kept at the constant: "no corpus regression" is not "no behaviour
> change". A few sites convert the sentinel to something OTHER than `"any"`
> (`x if x != "int" else ""`, and a widen-from-unknown guard at sema 1854);
> they now take the other arm and the corpus does not cover them.
>
> This does not by itself fix any `vm_*` probe. It ENABLES the boxing those
> probes need rather than completing it.

#### The obvious follow-on does NOT work yet (measured)

With the sentinel split, a bare `list` local resolves to `"any"` exactly like
an explicit `list[object]`, so the `box_element` gate could in principle drop
its `_explicit_object_lists` membership test. Tried it: **10 regressions
against 5 fixes, net 809 -> 804.**

    1105_async_gather      152_itertools_module   165_textwrap_module
    183_queue_module       233_queue_module       240_textwrap_module
    261_textwrap_module    270_queue_depth        int_prog_observer
    lib_queue_fifo

They cluster in modules that build a bare `list` and read its elements back
raw. `stdlib/queue.py` shows the shape -- a bare `list` LOCAL populated from a
bare `list` FIELD whose elements are already boxed, then assigned back to the
field:

```python
self._data: list = []          # field: elements already boxed
new_data: list = []            # local: elements were raw
for i in ...:
    new_data.append(self._data[i])
```

The precise mechanism has not been isolated, and is deliberately not guessed
at here. What is established is the shape of the constraint: **boxing the
write side without moving the read side is half a change**, and the half that
is missing costs more than the half that is present. The original gate's
comment said as much ("left raw so existing homogeneous-list code is
unaffected") and was correct.

So the read side moves first. That is the next unit of work, and it is bigger
than a gate flip: every path that consumes a list element -- subscript,
iteration, `pop`, slicing, passing the list onward -- has to agree on whether
elements are boxed, for a container whose element kind is `"any"`.

The narrative below is kept as written, because the reasoning that led here
is more useful than a tidied-up version of the conclusion.

`ast_nodes.UNKNOWN_TY` now exists, with the value `"int"` — deliberately
unchanged, so behaviour is identical and the corpus result must not move.

Its job is to separate the three meanings *at the source level first*. Sites
that mean "I don't know" say `UNKNOWN_TY`; the ones still saying `"int"` are
the ones that genuinely mean the integer type. Flipping the constant to a real
distinct value before that audit is finished would turn every un-migrated
`== "int"` comparison into a silent behaviour change instead of a visible one,
which is why it stays `"int"` for now.

The audit has two halves.

**Producer side -- sites that RETURN the sentinel: done.** Every helper
documented "'int' if unknown" is migrated -- `_list_el_type` (the one behind
`deque._data`), `_list_el_value_type`, `_dict_value_type_inner`,
`_dict_inner_value_type`, `_resolve_field_el`, `_resolve_field_inner_value`,
`_inparam_el_type`, `_outparam_el_type`. 70 `UNKNOWN_TY` uses in sema.py.

Two deliberate exceptions, both verified by reading rather than by pattern:

- `_gen_iter_elem_kind` keeps its `"int"`: `return "int" if range_args else ""`
  is a REAL int, because `range` yields ints.
- `_merge_el_type` keeps its `"int"`: `{a, b} == {"int", "bool"} -> "int"` is
  bool widening, not a sentinel. A mechanical sweep would have broken this
  one silently.

**Consumer side -- sites that COMPARE against it: 106 -> 86.** What remains
splits three ways, and only one of them is real work:

| | sema | ir_lower | codegen | disposition |
|---|---|---|---|---|
| workaround pairs | 2 | 11 | 8 | **deleted** by the split, not migrated |
| element-kind | few | 3 | 2 | need a behaviour decision (below) |
| genuine `int` | most | 19 | 15 | left alone |

The five element-kind sites are the actual remaining engineering:

```text
ir_lower.py:3382   if key_ty == "int":     # dict-key encoding
ir_lower.py:3490   if el_ty  == "int":     # set/dict key decoding
ir_lower.py:10833  if el_ty  == "int":     # list element decoding
codegen.py:12067   if A.expr_type(el) == "int":
codegen.py:12732   if el_t == "int":
```

Each has the shape

```python
if key_ty in ("str", "any"): ...   # handled
if key_ty == "int":                # unknown lands HERE and is encoded as an integer
```

so an unknown-kind value is silently treated as an int. Fixing them means
deciding what unknown should do there -- almost certainly box-and-tag-dispatch,
matching what `"any"` already does one branch up -- rather than renaming
anything. That decision is what the constant flip is waiting on.

**Site 1, worked through -- and the first two analyses of it were wrong.**
Recording the sequence, because each wrong turn was caught by a probe rather
than by reading, and that is the argument for writing the probe first.

*First analysis:* `_lower_dict_key` documents "everything else -> repr()", so
unknown keys belong in the repr arm; narrow the `key_ty in ("str", "any")`
test to `"str"`. **Wrong** -- a key written through the `str` arm (bare) and
read back through the `any` arm (repr, quoted) would land in different slots,
breaking str/any key interop.

*Second analysis:* group them as-is, since both pass through bare. **Also
wrong**, and the probe shows it. This is CPython-correct code:

```python
d = {}; d["foo"] = 7
def get(d, k): return d[k]
print(get(d, "foo"))            # CPython: 7   asmpython: crash
```

The function's own docstring states the assumption that fails: "an 'any' key
is already a real pointer; the runtime hashes whatever string it points at."
That predates boxing. A value in an "any" slot is a 24-byte BOX cell, not a
string, so the runtime hashed the cell and never matched the key the `str` arm
wrote. Fixed by `_lower_encode_any_key`, which dispatches on the runtime tag
and reproduces each static arm's encoding exactly -- str-tagged boxes yield
their PAYLOAD bare (matching the `str` arm), everything else goes through
`_lower_format_any_value(repr_mode=True)` (decimal for int, repr for the
rest).

*What the probe still needs.* Fixing the key encoding is not sufficient,
because the probe never reaches it. `d` is unannotated too, so the subscript
takes the `obj_ty == "any"` path, where only a statically-`str` index is
treated as a dict access:

```python
if obj_ty == "any" and A.expr_type(e.index) == "str":   # dict get
    ...
# otherwise: fall through and treat it as a LIST integer index
```

`k` is typed `"int"` -- the unknown sentinel -- so an unknown key is read as a
list index, which is where "list index out of range" comes from. Making that correct needs a runtime dispatch on the CONTAINER's tag -- which
was implemented, tried, and **reverted**, because it cannot work yet:

The container reaching this path is an UNTAGGED raw pointer. Dicts *are* in
`_BOXABLE_STATIC_TYPES`, but the argument is never boxed at the call boundary,
because the parameter carries the `"int"` UNKNOWN sentinel rather than `"any"`.
And an untagged pointer is indistinguishable between a dict and a list at run
time -- `_lower_read_any_tag` returns `UNTAGGED_ID` for both. So the dispatch
sent dicts down the list arm regardless, turning a clean "list index out of
range" into a segfault. Strictly worse, so it is gone.

**This is the first probe proven to be blocked ON the flip rather than merely
adjacent to it.** No read-site fix exists: the container has to be boxed
before anything can tell what it is, and boxing unknown values is exactly what
splitting UNKNOWN out of `int` delivers.

`tests/cases/vm_dict_key_through_any.py` pins the whole chain.

### The invariant that made both wrong turns wrong

> **An `"any"`-typed value is NOT guaranteed to be boxed.**

`_lower_box_any` wraps scalars and containers in a tagged cell, but coverage is
incomplete: a bare `list` annotation resolves to the `"int"` unknown-sentinel
rather than `"any"`, so nothing boxes its elements. Iterating one yields RAW
values that then flow into `"any"` slots untagged.

Any read-side change that assumes "this is `any`, therefore it carries a tag"
silently corrupts the un-boxed half. That single assumption produced both of
this session's regressions, six cases in total:

| change | broke | why |
|---|---|---|
| bare `0` renders as `None` in `_fe_tagged` | 152_itertools_module, 451_generator_yield_in_if, lib_collections_deque, lib_collections_deque_extend | raw unboxed `0`s reach it, so `[-1, 0, 1, 2]` printed `[-1, None, 1, 2]` |
| re-encode `"any"` dict keys by tag | 221_struct_depth, 485_bare_dict_get_default_counting | raw str pointers read UNTAGGED and got `repr`'d, missing the slot the `str` arm wrote |

The rule that fixes both: branch on the tag being inside the box range
`[set(-8), int(-1)]` and pass everything else -- UNTAGGED, null, real
instances -- through **unchanged**. `_lower_unbox_any` already uses exactly
that range test; copy it rather than testing individual tags.

Neither was caught by a filtered run. Both surfaced only in a full
`baseline --check`, because the breakage lands in unrelated stdlib and
container cases rather than near the code being changed.

This is also the strongest argument for the phase ordering: these are symptoms
of unknown values not being boxed at all. Splitting UNKNOWN out of `int` is
what removes the un-boxed half, and until it does, every read-side fix has to
carry a raw-value fallback.



### `None` and `bool` have no static type

`vm_none_is_not_zero`, `vm_bool_is_not_int`, and the last element of
`vm_container_heterogeneous` all reduce to one fact, stated in
`ast_nodes.is_none_expr`'s own docstring:

> True if `e` statically evaluates to `None` (**parsed as IntLit(0)**), even
> though its `expr_type` is "int".

`None` is not a value in the type system. It is the integer literal `0` carrying
an `is_none` flag, and that flag only survives on literals and directly-bound
names. `bool` is the same shape via `is_bool_expr`. So:

```python
def maybe(flag):
    if flag: return 0
    return None

maybe(False) is None     # True  (correct by accident)
maybe(True)  is None     # True  (WRONG -- 0 and None are the same value)
```

Because both branches are statically `"int"`, nothing is boxed, and the
information needed to tell `0` from `None` is gone before any runtime dispatch
could use it.

**A runtime-only fix is not possible, and attempting one caused a regression.**
Rendering a bare `0` as `None` in the tagged-element formatter was tried; it
turned `[-1, 0, 1, 2]` into `[-1, None, 1, 2]` and broke
`152_itertools_module`, `451_generator_yield_in_if`, `lib_collections_deque`,
and `lib_collections_deque_extend`. It has been reverted and the reasoning left
in a comment at that site so it is not retried. The original code's choice to
render `0` was a deliberate, documented trade-off, and it is the correct one
until `None` has a type of its own.

Blast radius of the hack: **29 `is_none_expr` sites, 27 `is_bool_expr` sites**
across `ast_nodes.py`, `sema.py`, `ir_lower.py`, `codegen.py`.

### Boxing coverage gaps

- `vm_str_field_via_helper` — an attribute read on an `any`-typed receiver
  loses the *field's* type. The receiver is a class instance (passed through
  unboxed, since instances carry their own `__class__`), but nothing tells the
  read site that `.tag` is a `str`, so it prints the pointer. Needs either
  per-class field-type dispatch on the runtime class id (the mechanism
  `dynamic_classvar_compat_fixes` already uses for class variables) or boxed
  field storage.
- `vm_tuple_through_any` — the tuple is boxed, but its *elements* are stored
  raw, so reading one back through `any` yields an untagged pointer.
- `vm_callable_param_result` — an indirect call through a function-pointer
  parameter has no return type.

### `round(x, ndigits)` needs exact decimal conversion

The current lowering scales by `10**n`, applies SSE `roundsd`, and unscales.
`2.55 * 10` rounds to exactly `25.5` in binary, and ties-to-even then gives
`26` → `2.6`, where CPython gives `2.5`.

A `sprintf("%.*f")` + `strtod` round-trip is the natural fix and gets `2.55`
right, but **msvcrt rounds half-away-from-zero while CPython rounds
half-to-even**, measured directly:

| value | msvcrt | CPython |
|---|---|---|
| `%.2f` of `0.125` | `0.13` | `0.12` |
| `%.0f` of `2.5` | `3` | `2.0` |

and msvcrt cannot supply the exact decimal expansion (it zero-fills past 17
significant digits), so the tie cannot be detected from its output either.
Correct `round` therefore needs a real `dtoa` in the runtime — the same
component the `%f`-accuracy note in §2 needs.

### `bytes` / `bytearray` / arbitrary-precision `int`

These are absent types, not broken ones — new feature work, separable from the
value model and from each other.

---

## 4. Estimate, with actuals

Two items are now measured rather than guessed, and both moved.

| work | est. | actual |
|---|---|---|
| 1. Split UNKNOWN out of `int` | 3-5 wks | **DONE** -- 5 commits, zero regressions, +4 cases |
| 2. Boxing coverage (read side must move first) | 2-3 wks | started; write-side-only measured as a net -5 |
| 3. `None` + `bool` as real static types | 2-3 wks | not started |
| 4. `dtoa` in the runtime | 2-3 wks | not started |
| 5. `bytes` / `bytearray` | 3-4 wks | **re-scoped up** -- see below |
| 6. Arbitrary-precision `int` | 4-6 wks | not started |

**Item 1 came in far under.** The audit was the work; the flip was one line and
free. The lesson generalizes: for a type-system change in this codebase, cost
is dominated by auditing the sites that PRODUCE a type, not by the change
itself.

**Item 5 is bigger than it looks, in the opposite direction.** `bytes` and
`bytearray` are not absent -- they are implemented as `list[int]` and work
correctly for `len`, indexing and mutation. Only `repr` is wrong
(`[97, 98, 99]` where CPython gives `b'abc'`), which reads like a small fix.
It is not: `sema` maps `bytes` to the static type `"list"`, so making `repr`
correct requires a distinct static type, and that means auditing **145
`== "list"` comparison sites** -- more than the 106 the `int` split needed.

There is no cheap correct shortcut. Tagging the AST node (`is_bytes`, the way
`is_bool`/`is_none` work today) would fix `print(b"abc")` for a literal and
fail the moment the value passes through a variable -- which is exactly the
defect §3 is about. Doing it properly is the only option that does not add a
fourth side-channel flag to `IntLit`'s three.

**Revised total for Phase 1 proper (items 1-3): 5-8 weeks remaining**, item 1
being complete. Items 4-6 stay independently schedulable, with item 5 now
looking closer to 4-6 weeks than 3-4.

