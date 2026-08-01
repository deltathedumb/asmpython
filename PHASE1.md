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

## 3. Not fixed, and precisely why

### `None` and `bool` have no static type — this is the real blocker

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

## 4. Revised estimate

The original plan priced Phase 1 at 3–4 months to build a value representation,
type lattice, and object model. The representation already exists on the live
backend, so the remaining work splits differently:

| work | est. |
|---|---|
| `None` + `bool` as real static types (parser, sema, ir_lower, ~56 dependent sites) | 4–6 wks |
| Boxing coverage: `any`-receiver field reads, tuple/container elements, indirect-call returns | 3–4 wks |
| **Phase 1 proper** | **7–10 wks** |
| `dtoa` in the runtime (fixes `round`, `%f` accuracy, float `repr` past 17 digits) | 2–3 wks |
| `bytes` / `bytearray` | 3–4 wks |
| Arbitrary-precision `int` | 4–6 wks |

The last three are independently schedulable features rather than foundation
work, and each has its own probe already pinned.

`None`-with-a-type is the highest-leverage item and should go first: it is the
sole blocker on three probes, it is what makes the existing boxing machinery
apply to the one value it currently cannot represent, and every workaround
around it (`is_none_expr`, `is_bool_expr`, the `0`-vs-`None` formatter
trade-off) is deleted by it.
