# Changelog

All notable changes to asmpython are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [Unreleased]

CPython-parity expansion: making common idioms compile and produce correct
output rather than silent miscompilations.

### Added

- **Multiple context managers in one `with` (`with a as x, b as y: body`).**
  Desugars at parse time into nested `with a as x: with b as y: body`,
  matching CPython's enter/exit ordering exactly (`a` is entered before `b`;
  `b` is exited before `a`, even if `body` raises) -- no sema/codegen changes
  needed beyond the single-context-manager support below.
- **`with` statements / context managers (`__enter__`/`__exit__`).**
  `with expr as name: body` (and `with expr: body` without `as`) requires
  `expr`'s class to define both `__enter__` and `__exit__` -- a compile-time
  error otherwise (`'C' object does not support the context manager protocol
  (missing __enter__/__exit__)`). It's rewritten in place into a
  `try`/`finally` (reusing the existing `setjmp`/`longjmp` unwinding), so
  `__exit__` runs even if `body` raises. asmpython has no rich exception
  objects, so `__exit__` is always called as `__exit__(None, None, None)` --
  it can run cleanup but can't inspect or suppress the exception. Multiple
  context managers in one `with` (`with a, b:`) is not yet supported. New
  general `FuncSig.returns_self` inference (a method with no return
  annotation whose only `return`s are `return self`, e.g. `__enter__`,
  infers `instance:<ClassName>` at call sites instead of `int`) -- useful
  for any self-returning builder-style method, not just `__enter__`.
- **`str.format()` named fields (`"{name}".format(name="bob")`).** Named
  replacement fields now work alongside positional `{}`/`{0}` fields and the
  full `!conv`/`:format-spec` mini-language, e.g.
  `"{name} is {age}".format(name="bob", age=5)`,
  `"{val:,}".format(val=1234567)`. Referencing an undefined keyword name, or
  a positional index beyond the given positional arguments, is now a
  semantic error instead of crashing the compiler. Attribute/index access in
  fields (`{0.attr}`, `{0[0]}`) remains unsupported and now raises a clear
  semantic error rather than being silently misparsed.
- **Zero-pad width + grouping for f-strings (`f"{n:015,}"`).** Combining a
  zero-padded width with a `,`/`_` grouping option now matches CPython's
  separator-aware zero-padding (`f"{1234567:015,}"` -> `"000,001,234,567"`),
  for both `int` and `float` (`f"{pi:015,.2f}"`). Previously the grouping
  option was silently dropped in this combination. New runtime helper
  `_runtime_group_digits_zeropad` computes the smallest digit count whose
  grouped form reaches the requested width, zero-pads to that count, then
  reuses `_runtime_group_digits` for the actual grouping.
- **`@property` setters (`@x.setter`).** A method decorated `@x.setter`
  (matching the name of an `@property` getter `x`) makes `obj.x = value`
  dispatch to that setter instead of writing an instance-dict field directly,
  matching CPython. Setters participate in inheritance and virtual dispatch
  like any other method (a subclass may also override the getter and/or
  setter). Assigning to a property with no matching setter is still a
  compile-time error (`property 'x' of 'C' object has no setter`).
- **Dict literal unpacking `{**d1, "k": v, **d2}` (PEP 448).** A dict literal
  may contain zero or more `**other` spreads alongside explicit `key: value`
  pairs, in any order and any number of times; each `other` must be
  dict-typed (sema error `dict unpacking requires a dict (got ...)` otherwise).
  Entries are merged in source order via `_runtime_dict_update`, so later
  entries (whether spreads or explicit keys) win on key conflicts, matching
  CPython exactly; `{**d1}` is a shallow copy and no operand (literal or
  spread source) is mutated. Represented in the AST as a `None` entry in
  `DictLit.keys` paired with the spread expression at the same index in
  `DictLit.values`. The parser treats a leading `**` inside `{...}` as
  unambiguously a dict literal, since set literals cannot contain `**expr`.
- **Dict union operators `d1 | d2` and `d1 |= d2` (PEP 584).** `d1 | d2`
  builds a fresh dict containing `d1`'s entries with `d2`'s merged on top
  (`d2`'s values win on conflicting keys; neither operand is mutated), and
  chains like `{"x": 1} | {"x": 2} | {"y": 3}`. `d1 |= d2` merges `d2` into
  `d1` in place (the dict header is unchanged). Both lower to the existing
  `_runtime_dict_update` helper, reusing the same "build a fresh dict and
  update twice" codegen as set union (`|` on two sets). Sema infers the
  result's value type from whichever operand has a known (non-default) value
  kind, so `d3 = d1 | d2` followed by `d3[k]` type-checks correctly; `d |=
  <non-dict>` is a sema error (`unsupported operand type for |=: dict |=
  ...`). `expr_type()` for a `BinOp` now also honors a `"dict"`
  `inferred_type` (previously only `type`/`any`/`set` were honored, so a
  dict-union `BinOp` fell through to the bitwise-int default and any
  subscript on its result failed with "cannot index a int").
- **Starred assignment unpacking `a, *rest = xs` (PEP 3132).** A single
  `*name` target may appear anywhere among a tuple-assignment's targets
  (`a, *rest = xs`, `*init, last = xs`, `first, *mid, last = xs`) when the
  right-hand side is a `list`. The starred target binds to a fresh list of
  the same element kind holding the leftover elements; plain targets before
  and after it read the corresponding front/back elements directly. New
  `A.StarTarget` AST node (parser-only; only valid as a `TupleAssign`
  target). At most one starred target is allowed, and a lone `*name = ...`
  is rejected (`starred assignment target must be in a list or tuple`),
  matching CPython's `SyntaxError`s.
- **`enumerate(iterable, start)`.** The optional second argument sets the
  initial value of the index variable, matching CPython:
  `for i, x in enumerate(xs, 1): ...` numbers `x` from 1 instead of 0.
  `start` may be any `int`-typed expression (literal, variable, or
  negative). Sema now accepts 1 or 2 arguments to `enumerate()` in the
  `for i, x in enumerate(...)` loop form; codegen evaluates `start` into a
  fresh local once before the loop and adds it to the 0-based iteration
  counter when writing the index variable each pass.
- **The walrus operator `:=` (assignment expressions, PEP 572).**
  `target := value` evaluates `value`, binds it to `target` exactly like a
  plain `target = value`, and the whole expression yields that value —
  e.g. `if (n := len(data)) > 3:`, `while (line := f()) :`, or
  `x if (n := len(s)) > 3 else y`. New `A.NamedExpr` AST node; the
  `A.Assign` target-binding logic in sema is now shared via
  `_bind_name_from_value` so `:=` infers types (including list/dict/tuple
  element kinds, and the bool/None print flags from the bool/None fix
  above) the same way `=` does. Inside a list/dict comprehension, the
  walrus target binds in the *enclosing* scope rather than the
  comprehension's own (per PEP 572) — a new `_merge_walrus_bindings` copies
  any such new bindings out of the comprehension's child scope after
  type-checking it, so `results = [y := v * 2 for v in vals]` leaves `y`
  usable (and correctly typed) afterward.
- **Container repr for `print()` and `str()`.** `print(x)` / `str(x)` now
  render Python-style output for every built-in container instead of a raw
  pointer or compile error:
  - lists: `[1, 2, 3]`, `['a', 'b']`
  - dicts: `{'a': 1, 'b': 2}`
  - tuples: `(1, 2, 3)`, `(42,)` (trailing comma for 1-tuples), mixed kinds
  - sets: `{1, 2}` / `set()` when empty

  Backed by shared `_runtime_fmt_elem` + `_runtime_{list,dict,set}_repr`
  helpers; tuples are unrolled inline to honor per-slot element kinds.
- **`range()` as a first-class value.** `list(range(n))`, `sum(range(...))`,
  `len(range(...))`, and bare `range(...)` now work (materialized to a
  `list[int]` via `_runtime_range_list`), with 1/2/3-arg and negative-step
  forms. The `for x in range(...)` fast path is unchanged.
- **`str(container)`** stringifies lists/dicts/tuples/sets via their repr.
- **`str.format()`** with a literal format string: positional `{}`
  (auto-numbered), explicit `{0}`/`{1}` (including reuse), and escaped
  `{{`/`}}`. Previously it silently returned `0`.
- **f-string format specs** are honored: `f"{x:.2f}"`, `f"{n:05d}"`,
  `f"{n:x}"` (float `.Nf/.Ne/.Ng`; int `d/x/X/o` with width and zero-pad).
  Specs were previously stripped and ignored.
- **f-string format-spec alignment/fill/width** (`[[fill]align]width`, the
  general prefix from Python's format-spec mini-language): `align` is
  `<`/`>`/`^` (left/right/center), with an optional `fill` character before
  it (default space) — `f"{name:>10}"`, `f"{name:*^11}"`, `f"{n:0>5}"`.
  Works for `str`, `int`, `float`, and `bool` segments, and combines with
  numeric specs (`f"{pi:>10.2f}"`). A bare width with no align character on
  a `str` segment defaults to left-alignment, matching CPython. New
  `_split_fmt_align`/`_split_fmt_width`/`_gen_fstring_aligned` helpers
  (codegen.py) pad the formatted value via the existing
  `_runtime_str_{ljust,rjust,center}` runtime helpers. Also fixes
  `print(f"...")`, which previously lowered each segment to a direct
  `printf("%s", ...)` call that bypassed format-spec handling entirely
  (`_emit_print_value` now routes any segment with a non-empty `fmt_spec`
  through the shared f-string segment formatter, for `str` too, not just
  `int`/`float`).
- **f-string binary format spec `b`/`#b`** for `int` values: `f"{n:b}"`,
  `f"{n:#b}"` (with a `0b` prefix), and zero-padded widths like
  `f"{n:#010b}"` (`-0b00101010`-style, with sign and prefix counted toward
  the width, matching CPython). New `_runtime_int_to_binary` runtime helper
  and `_parse_binary_spec`/`_gen_int_value_str`/`_emit_int_to_binary_str`
  codegen helpers; combines with the `[[fill]align]width` alignment support
  above (`f"{n:*>10b}"`). C's `printf` has no binary conversion, so this
  required a dedicated runtime helper (unlike `d`/`x`/`X`/`o`, which map to
  printf formats).
- **f-string grouping options `,`/`_`** (PEP 378/515 thousands separators)
  for `int`/`float` values: `f"{1234567:,}"` -> `"1,234,567"`,
  `f"{1234567:_}"` -> `"1_234_567"`, `f"{amount:,.2f}"` ->
  `"1,234,567.89"`, combinable with `d` and with alignment/width
  (`f"{n:>15,}"`). New `_runtime_group_digits` runtime helper (inserts the
  separator every 3 digits in the integer part, after any `-` sign and
  before any `.` fraction) and `_strip_grouping_option`/`_emit_group_digits`
  codegen helpers. **Known gap**: combining a grouping option with a
  zero-padded width (`f"{n:015,}"`) zero-pads to the requested digit count
  but omits the separators (`"000000001234567"`), unlike CPython's
  separator-aware zero-padding (`"000,001,234,567"`) -- the grouping option
  is dropped in this combination rather than producing the wrong total
  width.
- **f-string `.precision` for `str` segments**: `f"{name:.5}"` truncates to
  the first 5 characters (a no-op if shorter), combinable with
  alignment/width and the `s` type char (`f"{name:>10.5}"`,
  `f"{name:10.5s}"`). New `_runtime_str_truncate` runtime helper and
  `_split_str_width_precision` codegen helper.
- **`str.format()` now supports the full format-spec mini-language and
  `!r`/`!s`/`!a` conversions**, reusing the same machinery as f-strings:
  `"{:>10}".format(name)`, `"{0:.2f}".format(pi)`, `"{:08b}".format(n)`,
  `"{:,}".format(1234567)`, `"{!r}".format(name)`, `"{0!r:>8}".format(name)`.
  Each `{field}` is parsed into `(index, spec, conv)`, which are stamped
  onto the referenced argument expression (as `fmt_spec`/`conv_flag`) before
  delegating to `_gen_fstring_segment`. Previously a `:spec` after the field
  index was silently discarded and `!conv` would raise a compiler error
  (`int("0!r")`).
- **f-string conversions** `!r`/`!s`/`!a`: `f"{x!r}"` formats `x` via
  `repr()` (strings get quoted; a user class's `__repr__` takes priority over
  `__str__`), `!a` behaves like `!r`, and `!s` is the (already-default) `str()`
  conversion. Previously the conversion was silently dropped.
- **`@staticmethod`** methods are callable on the class
  (`ClassName.method(args)`), with no implicit receiver. `@classmethod` is
  accepted (call/dispatch work; class-state mutation through `cls` pending).
- **Class variables** (`class C: x = 5`, non-`@dataclass`) are static
  constants: read, write, and augmented assignment via `ClassName.x`.
- **`--target freestanding16`** — a raw, BIOS-bootable disk image. A 16-bit
  real-mode boot sector (ending in `0xAA55`) loads the kernel via INT 13h,
  enables A20, and switches 16 → 32 → 64-bit long mode, then runs the same
  64-bit kernel as `freestanding`. Verified booting under QEMU
  (`-drive format=raw,file=out.img`). Reuses the entire freestanding runtime.
- **`stdlib.math`** gains `trunc`, `nearbyint`, `asinh`/`acosh`/`atanh`,
  `exp2`/`expm1`/`log1p`, and the two-argument `copysign`, `remainder`,
  `fdim`, `fmax`, `fmin` — all thin bindings over C99 libm (present in
  msvcrt/ucrt on Windows).
- **`stdlib.os`** gains `fflush`, `feof`, `ftell`/`fseek`/`rewind` (file
  positioning), and `rename`.
- **`asmlib.hardware`** gains `rdrand` (hardware RNG), `io_wait`, and a new
  control/MSR group: `read_cr0`/`read_cr2`/`read_cr3`/`read_cr4`,
  `write_cr3`, `read_msr`/`write_msr`, `invlpg`, and `lidt` — building blocks
  for paging and IDT setup on `--target freestanding`.
- **`*expr` argument unpacking at call sites** (`f(*t)`, `obj.method(*t)`).
  `expr` must be a tuple of statically-known shape (a name, subscript, or
  attribute bound to a tuple literal or a `list[tuple[...]]` element); sema
  splices each slot in as its own positional argument before codegen, so no
  runtime varargs machinery is needed. Unpacking a `Call` result directly
  (`f(*g())`) isn't supported yet — assign it to a variable first.
- **`str.capitalize()`, `str.swapcase()`, `str.title()`** — new runtime
  helpers following the existing `upper`/`lower` pattern, including CPython's
  word-boundary rules for `title()` (any non-alpha character, including
  digits and apostrophes, starts a new word).
- **`str.zfill(width)`, `str.ljust/rjust/center(width, fillchar=' ')`** —
  numeric/text padding methods. `zfill` preserves a leading `+`/`-` sign when
  inserting zeros; `center` reproduces CPython's odd-padding split
  (`left = marg // 2 + (marg & width & 1)`).
- **`str.rpartition(sep)`** — like `partition`, but splits at the *last*
  occurrence of `sep`; returns `("", "", s)` when `sep` is absent (the mirror
  of `partition`'s `(s, "", "")`).
- **`str.removeprefix(p)`, `str.removesuffix(s)`, `str.casefold()`** —
  `removeprefix`/`removesuffix` strip the given affix only if present
  (otherwise return an unchanged copy); `casefold` is implemented as ASCII
  `lower`.
- **`hex(n)`, `oct(n)`, `bin(n)`** now actually convert (previously these were
  accepted by sema but produced a null string at runtime, printing `(null)`).
  Backed by a shared `_runtime_int_to_base` helper; matches CPython's
  `"0x1a"`/`"0o32"`/`"0b11010"` formatting, including the leading `-` for
  negative inputs.
- **`divmod(a, b)`** — returns the `(a // b, a % b)` tuple (int operands),
  using the same floor-division semantics as `//`/`%`. Previously undefined.
- **Bare `raise`** (re-raise the currently-active exception) inside an
  `except` block. `_runtime_exc_msg` is saved/restored around each
  `try`/`except` so a bare `raise` after the exception has been fully
  handled correctly reports `RuntimeError: No active exception to reraise`,
  matching CPython, instead of resurrecting a stale message.
- **`%` printf-style string formatting**: `"...%s/%d/%f..." % (args)` with a
  literal format string on the left and a tuple (or single value) on the
  right. Supports `%s`, `%r`, `%d/%i/%u`, `%o/%x/%X`, `%e/%E/%f/%F/%g/%G`, and
  `%%`, with flags/width/precision (`%05d`, `%-10s`, `%.2f`, etc.), lowered to
  the same concat-chain machinery as f-strings. `%r` formats via `repr()`
  (same as an f-string's `!r`).
- **`sorted()`, `list.sort()`, `min()`, `max()` now support `key=` and
  `reverse=`.** `key=` may be a lambda literal or a name bound to a lambda
  (returning `str` or `int`); `reverse=True` reverses the result in place
  after sorting. `min()`/`max()` over a single iterable now report the
  iterable's actual element type (previously always `any`) and correctly
  compare `list[str]` elements via string comparison instead of raw pointer
  values. `key=` on the variadic `min(a, b, ...)`/`max(a, b, ...)` form, and
  bare function references as `key=` (e.g. `key=len`), are rejected with a
  clear compile error rather than miscompiling.

### Fixed

- **Unannotated function/method parameters infer their type from call-site
  arguments instead of silently defaulting to `int`.** Idiomatic Python
  rarely annotates parameters (`def __init__(self, name): self.name = name`,
  `def greet(msg): return msg`); previously every such parameter -- and any
  `self.x = param` field it fed -- defaulted to `int`, so e.g.
  `Resource("a").name` printed a raw pointer value instead of `"a"`. Now,
  when every call site that passes a syntactically-typed argument (a string/
  float/list/dict/tuple literal, f-string, or constructor call) agrees on one
  type, that type is adopted for the parameter (and, transitively, for
  `self.<field>` and for the function's own return type when it returns that
  parameter unchanged, e.g. `def greet(msg): return msg`). Parameters with no
  such evidence, or with conflicting evidence across call sites, keep the
  previous `int` default -- callers needing a different type still annotate
  explicitly. New `Analyzer._infer_unannotated_params` /
  `_infer_unannotated_returns`, run before field-type collection.
- **`try`/`except` now dispatches on the actual exception type**, including
  multiple `except` clauses with different types, tuples of types
  (`except (TypeError, ValueError):`), and the builtin exception hierarchy
  (`except LookupError:` catches a raised `KeyError` or `IndexError`;
  `except ArithmeticError:` catches `ZeroDivisionError`/`OverflowError`,
  etc.). Previously, when a `try` had more than one `except` clause, the
  *first* handler's body always ran regardless of the raised exception's
  type or whether its declared type matched — e.g. `except TypeError: ...
  except KeyError as e: ...` would run the `TypeError` body even for a
  raised `KeyError`. Every exception now carries a runtime type id
  (`_runtime_exc_type`); each handler's declared type(s) are checked against
  it in source order, and if none match, `finally` still runs and the
  exception propagates to an enclosing handler. A bare `except:` and a
  `raise` of a plain string continue to match unconditionally (preserving
  prior behaviour for untyped raises). New AST fields `A.Try.handler_types`
  / `extra_handlers: list[(types, bind_name, body)]`; sema validates each
  `except` type name is a builtin exception or a user class deriving from
  one.
- **Integer `//` and `%` now floor toward `-inf` like Python**, not toward
  zero like x86 `idiv`. Previously `-7 // 2` gave `-3` (and `-7 % 2` gave
  `-1`); now both match CPython (`-4` and `1`). The fix is a single shared
  adjustment in `_emit_binop_inline` (covers both binops and augmented
  assignment): if the truncated remainder is nonzero and its sign differs
  from the divisor's, decrement the quotient and add the divisor to the
  remainder.
- **Nested-container element types are tracked** through subscript and
  for-loop binding: `people[i]["k"]`, `for p in people: p["k"]` (list[dict]),
  `grid[i][j]` (list[list]), and tuple unpacking `for a, b in pairs`
  (list[tuple]) no longer print raw pointers.
- **Dicts now iterate in insertion order**, matching CPython 3.7+. Previously
  `print(d)`, `for k in d`, `.keys()`/`.values()`/`.items()`, and dict
  comprehensions walked the open-addressed hashtable in bucket order (FNV-1a
  slot order), which generally differed from insertion order and from
  CPython's output. The dict/set/instance header gains a new `order_buf`
  field (an array of `cap` key pointers; the first `len` are the live keys in
  insertion order), bumping the header from 32 to 40 bytes.
  `_runtime_dict_set` appends new keys to `order_buf`; `_runtime_dict_grow`
  copies it into the larger buffer unchanged; `_runtime_dict_pop` finds and
  removes the popped key's entry, shifting later entries left to close the
  gap. `_runtime_dict_keys`/`_runtime_dict_values`/`_runtime_dict_items`,
  `_runtime_dict_repr`, `_runtime_dict_update`, and `for k in dict`/`for x in
  set` all now walk `order_buf[0..len)` instead of the hashtable's bucket
  order. As a side effect, `_runtime_dict_update` (used by `|`/`|=` and
  `{**a, **b}`) now merges new keys from `src` in *its* insertion order,
  matching CPython's dict-merge semantics exactly. Set iteration order is
  unaffected in spirit (CPython doesn't guarantee it either), but now also
  follows each set's insertion order rather than bucket order.

- **`str(int)` / `str(float)` no longer alias a shared buffer.** Storing
  several conversions (e.g. `[str(x) for x in xs]`) previously made every
  element show the last value (`['3', '3', '3']`); each now gets a fresh copy.
- **Lambdas bound to a name are now callable.** `f = lambda x: x + 1; f(41)`
  and lambdas passed as arguments returned `0`; indirect calls through a
  local/global/parameter function pointer now work, and a name-bound lambda's
  call result is typed from its body (so str-returning lambdas print right).
- **`abs(float)`** returns a float again instead of printing its raw bits.
- **`time.difftime`** is now typed `float` (C's `difftime` returns a `double`
  in `xmm0`); declaring it `int` read the wrong register and produced garbage.
- **`del xs[i]`** now actually removes the element, shifting later elements
  down and shrinking the list (negative indices supported). **`del d[k]`**
  is now correctly wired up too (the dict-pop call existed but its key slot
  was never reserved, so it silently did nothing). Previously both forms
  compiled and ran without error but left the container unchanged.
- **`print()`/`str()` of nested containers** (`list[list]`, `list[dict]`,
  `dict[str, list]`, `dict[str, dict]`, one level deep) now recurse into the
  element/value repr instead of printing a raw pointer, e.g.
  `print([[1, 2], [3, 4]])` -> `[[1, 2], [3, 4]]` and
  `print({"a": [1, 2]})` -> `{'a': [1, 2]}`. `_runtime_fmt_elem` now carries
  an inner-kind nibble so it can call back into `_runtime_list_repr` /
  `_runtime_dict_repr` for container-typed elements.
- **`dict[str, T]` for `T` other than `int`** (`str`, `float`, or a nested
  container) now reprs correctly when read off a plain variable —
  `print(d)` for `d = {"a": "x"}` previously printed the raw string pointer
  as an integer because the value kind wasn't propagated onto the `Name`
  node.
- **Float values stored in dicts** (`{"a": 1.5}`, `d["a"] = 1.5`,
  `d.get("a")` / `d.get("a", 1.5)`) now round-trip the IEEE-754 bit pattern
  correctly. Previously these paths copied whatever was in `rax` (not
  `xmm0`, where float results actually live) into the dict slot, so any
  `dict[str, float]` value read back as garbage.
- **Whole-number floats print with a trailing `.0`**, matching CPython:
  `print(2.0)` -> `2.0` (was `2`), and likewise for list/dict elements and
  f-string interpolations. `sprintf`'s `%g` drops the decimal point for
  integral values; a new shared `_emit_float_repr_fixup` scans the result and
  appends `.0` unless it already contains `.`/`e`/`E` (a fraction or
  exponent) or `n`/`i`/`N`/`I` (`nan`/`inf`/`-inf`, left as-is).
- **`-0.0` now prints as `-0.0`, not `0.0`.** Unary `-` on a float negated by
  computing `0.0 - x`, but IEEE-754 `0.0 - 0.0` is `+0.0`, losing the sign.
  Negation now flips the sign bit directly (`xor` with `0x8000000000000000`).
- **`math.floor`/`math.ceil`/`math.trunc` now return `int`, matching
  CPython** (`math.trunc(3.7)` -> `3`, not `3.0`). Previously typed as
  `float` (the underlying libm functions return `double`), so `print(...)`
  the whole-number `.0` fix above would have made them mismatch CPython;
  the FFI call layer now supports an `f2i` return conversion
  (`cvttsd2si`) for libm functions whose Python-visible return type narrows
  to `int`.
- **`xs[i] = <float>` for `list[float]`** now stores the IEEE-754 bit pattern
  correctly (same `xmm0`-vs-`rax` issue as the dict fixes above). Previously
  `xs[1] = 9.5` corrupted the slot with whatever integer happened to be in
  `rax`, e.g. `[1.0, 2.0, 3.0]` became `[1.0, 4.94066e-324, 3.0]`.
- **Functions/methods with more than one `float` parameter, or a mix of
  `int`/pointer and `float` parameters, now compute correct results.**
  `def add(x: float, y: float) -> float: return x + y` called as
  `add(3.0, 4.0)` previously returned `8.0` instead of `7.0`; a class
  `__init__(self, x: float, y: float)` corrupted `self.x`/`self.y`. The
  caller side passed every argument — including floats — through the integer
  ABI registers (`rcx`/`rdx`/`r8`/`r9` or `rdi`/`rsi`/...), and the callee's
  prologue spilled them the same way, so float params never round-tripped
  through `xmm0`-`xmm3`/`xmm0`-`xmm7`. A single-float-param function "worked"
  only by accident (leftover `xmm0` state from the caller's last `movsd`
  survived the `call`). Both call sites and prologues now compute each
  argument's ABI register via a new shared `_assign_arg_regs`: Win64 assigns
  registers positionally (slot *N* is `xmmN` or the *N*th of
  `rcx,rdx,r8,r9`, depending on that argument's type), while SysV
  (Linux/freestanding) keeps separate integer (`rdi,rsi,rdx,rcx,r8,r9`) and
  float (`xmm0`-`xmm7`) counters. `_collect_locals` also now records each
  parameter's type in `local_types`, so reads of a float parameter inside the
  function body correctly use `movsd`/`xmm0` instead of `mov`/`rax`.
- **`**` (and `**=`) on `float` operands** now works, e.g. `2.0 ** 0.5`,
  `9.0 ** 0.5`, `x ** 2.0` for a `float` parameter `x`. Previously
  `_gen_binop_float`/`_emit_binop_inline_float` raised
  `NotImplementedError(f"float binop '**'")` for any non-integer base/exponent.
  Lowered to a call to libm's `pow(double, double)` via the existing
  `_emit_call_libc_double_double` helper (same calling convention as `fmod`
  for `%`); `pow` added to the Windows/Linux `extern` lists. Integer `**`
  (repeated-squaring) is unchanged. On the freestanding target, `**` with a
  float operand still uses the pre-existing `_runtime_math_pow` stub, which
  returns `0.0` (same known limitation as `sin`/`cos`/`exp`/etc.).
- **`set.discard()`, `set.remove()`, `set.copy()`, and `set.pop()`** are now
  implemented. Sema already accepted all four (typed `discard`/`remove` as
  `int`, `copy` as `set`, `pop` as `str`), but codegen raised
  `NotImplementedError(f"set.{e.method}() not implemented yet")`. `discard`
  checks membership via `_runtime_dict_contains` and removes via
  `_runtime_dict_pop` only if present; `remove` calls `_runtime_dict_pop`
  directly (raising `KeyError` if absent); `copy` is the same
  allocate-and-`_runtime_dict_update` pattern as `dict.copy()`; `pop` removes
  and returns the first live key from `_runtime_dict_keys`, raising
  `KeyError: 'pop from an empty set'` on an empty set.
- **Set literals/`.add()`/`.discard()`/`.remove()` with non-`str` elements now
  raise a compile-time `SemaError`** instead of segfaulting at runtime.
  `{1, 2, 3}` and `seen = set(); seen.add(1)` previously crashed: sets reuse
  the dict hash table, which hashes/compares keys as string pointers
  (`_runtime_hash_string` + `strcmp`); a raw `int` like `1` is read back as a
  pointer to address `0x1` and segfaults. Sets remain str-keyed in v1 — full
  `int`/`float`/etc. set-element support needs a tagged key representation
  (to disambiguate a boxed pointer from an inline scalar without colliding
  with the `0`=empty / `1`=tombstone sentinels), which is a larger follow-up.
- **`@property`** getters now work: `obj.x` (no call parens) where `x` is a
  `@property`-decorated method invokes the getter, typed from its return
  annotation. Previously `obj.x` always read an *instance field* named `x`
  — since `@property` methods never assign `self.x`, the field was absent
  from the class's field table and read as `0`/`any` every time (a silent
  miscompilation). Sema now resolves `obj.x` against the class's methods
  first; if `x` is `@property`, the `Attr` node is rewritten in place into
  an equivalent no-arg `MethodCall`, so codegen's existing dispatch — including
  virtual dispatch for a property overridden in a subclass — handles it for
  free. `@x.setter` is not modelled by the parser (decorators are captured as
  a bare dotted-name prefix, so `@area.setter` is indistinguishable from
  `@area`); assigning to a `@property` attribute (`obj.x = v`) now raises
  `property 'x' of 'Cls' object has no setter`, matching CPython's
  `AttributeError`, instead of silently creating an unrelated instance field.
- **Tuple-assignment targets can now be subscripts/attributes**:
  `xs[i], xs[j] = xs[j], xs[i]`, `self.x, self.y = self.y, self.x`, and mixes
  with plain names (`a, xs[0] = xs[0], a`) all work. Previously only bare
  names were accepted as targets; `xs[0], xs[1] = xs[1], xs[0]` was a parse
  error (`expected NEWLINE, got OP ','`). `TupleAssign.targets` is now a list
  of `Name`/`Subscript`/`Attr` expressions; the parallel-assignment codegen
  (evaluate every RHS into a scratch slot, then commit each store) reuses the
  same store sequences as `IndexAssign`/`AttrAssign`. The single-iterable
  unpack form (`a, b = some_list`) still requires plain-name targets and
  raises a clear error otherwise.
- **`type(x)` now returns a real `"<class '...'>"` string instead of crashing
  or printing a raw id.** Previously `type(x)` always treated `x` as an
  instance dict and looked up its `__class__` tag — for `int`/`float`/`str`/
  `list`/`dict`/`tuple`/`set` values (anything that isn't a dict-shaped
  pointer), this read garbage memory and segfaulted (e.g. `b = True;
  print(type(b))`). Now: for a statically-known builtin type, `type(x)`
  yields an interned `"<class 'int'>"`/`"<class 'list'>"`/etc. string; for a
  user instance it reads the RTTI class id as before and indexes a new
  per-class `.rodata` table of `"<class '__main__.ClassName'>"` strings
  (honoring inheritance, since the id is the *runtime* class). `print()`
  and `str()` of the result now match CPython's `repr()` for types. Opaque
  (`any`-typed) arguments keep the old raw-class-id fallback.
- **`bool` and `None` values now print/format as `True`/`False`/`None`**,
  matching CPython, instead of the underlying `1`/`0`/`0`. This covers
  `print()`, `str()`, `repr()`, and f-string interpolation of: `True`/`False`/
  `None` literals; variables assigned from them; comparisons (`a == b`,
  `1 < 2`, ...); `not x`; `and`/`or` of bool operands; a conditional expression
  (`x if c else y`) where both branches are bool; and `bool(x)`. `type(x)` for
  these values now also reports `<class 'bool'>` / `<class 'NoneType'>`
  (previously `<class 'int'>`). Bool/`None` remain represented as plain `int`
  (`0`/`1`) for arithmetic and comparisons — only the *rendering* changed.
  New `A.is_bool_expr`/`A.is_none_expr` static-analysis helpers (and
  `is_bool`/`is_none` flags threaded through `IntLit`/`Name` and `Scope`)
  drive the dispatch in `_emit_print_value`, `_gen_fstring_segment`, and the
  `str()`/`repr()` builtins.

---

## [1.0.2] — 2026-06-12

### Added

- **Linux self-host build on Windows** — `build.py` now produces both
  `build\asmpython.exe` (Windows) and `build\asmpython-linux` (Linux ELF) in
  one run. The Linux target is compiled inside WSL using its native `nasm` and
  `gcc`. `build.bat` is now a thin wrapper that invokes `build.py`.

### Changed

- **Toolchain on Windows must be on PATH.** `asmpython.bat` no longer bundles
  or downloads dependencies; it requires `python`, `nasm`, and `gcc` to be
  available on PATH. `_download-deps.bat` now fetches w64devkit instead of the
  WinLibs MinGW bundle.

### Fixed

- **Linux executables now link under modern gcc.** The Linux link step passes
  `-no-pie`; the generated code uses absolute relocations against libc symbols,
  which gcc's default PIE mode rejects.

---

## [1.0.1-hotfix1] - 2026-06-12

### Changed

- **`build.bat`** changed to compile for both Linux and Windows in one run.

---

## [1.0.1] — 2026-06-12

### Added

- **`--keep-assembly`** compiler flag — the intermediate `.asm` file is now
  deleted after assembling by default; pass `--keep-assembly` to retain it.
  `--emit-asm` is unaffected and still keeps the file as before.

### Changed

- **`build.bat`** simplified to a single purpose: self-compile asmpython with
  itself to `build\asmpython.exe`. General compilation, `--test`, `--selfhost`,
  and `--run` modes have been removed; use `asmpython.bat` directly for those.

---

## [1.0.0] — 2026-06-12

First stable release.

### Added
- **`--target freestanding`** — Multiboot1-compatible flat binary output (`-f bin`)
  via NASM with no external linker. Boots in QEMU with
  `qemu-system-x86_64 -kernel <output.bin>`.
- **Freestanding runtime**: VGA text mode, COM1 serial output (with `\r\n`),
  bump allocator (256 KB heap), 64 KB kernel stack, 32→64-bit long-mode setup,
  identity-mapped page tables (first 16 MB, 2 MB huge pages).
- **`stdlib.sys`** — `exit`, `getpid`, `getenv`, `abort`, `version`, `maxsize`.
- **`stdlib.time`** — `time`, `sleep`, `clock`, `difftime`.
- **`stdlib.random`** — `seed`, `rand`, `RAND_MAX`.
- **`asmlib`** — new comprehensive hardware/network/GUI library package.
  - `asmlib.hardware` — bare-metal port I/O (`in_byte`/`out_byte`/`in_word`/
    `out_word`/`in_dword`/`out_dword`), MMIO, `rdtsc`, `cpuid`, `halt`,
    `disable_interrupts`/`enable_interrupts`, PIC 8259A (`pic_eoi`/`pic_mask`/
    `pic_unmask`), PIT (`pit_set_freq`), PS/2 keyboard (`keyboard_read`/
    `keyboard_poll`), VGA color/cursor helpers. All implemented as inline
    NASM in the freestanding codegen; stub-returns-0 on hosted targets.
  - `asmlib.network` — BSD socket API: `socket`, `bind`, `connect`, `listen`,
    `accept`, `close`, `send`, `recv`, `send_all`, byte-order helpers, address
    helpers, constants (`AF_INET`, `SOCK_STREAM`, `PORT_*`). Helper symbols
    (`_net_bind`, `_net_connect`, etc.) implemented inline in the hosted
    codegens (Linux SysV ABI and Windows x64 ABI).
  - `asmlib.gui` — SDL2 bindings: window, renderer, draw calls (`draw_line`,
    `fill_rect`, `draw_rect`), event pump, timing. Helper symbols
    (`_gui_poll_event`, `_gui_fill_rect`, etc.) implemented inline in hosted
    codegens via SDL_Rect stack allocation and static event-state buffers.
- **`Assembly` class** (stdlib.assembly) — 150+ x86-64 instruction builder
  methods, SSE/AVX, atomics, system calls, full directive set.
- **`pyproject.toml`** — project is now pip-installable (`pip install .`).
- **`examples/`** — curated example programs moved from root into a dedicated
  directory.
- **`docs.html`** — polished single-file reference documentation.

### Changed
- VGA `_vga_putchar` now mirrors all output to COM1 serial (with `\r\n`
  conversion on newlines) so freestanding programs are testable headlessly
  with `qemu … -serial stdio`.
- `_vga_attr` BSS variable controls the current VGA text attribute byte;
  defaults to `0x07` (light-grey on black) when zero.
- Freestanding section ordering fixed: `_load_end` label now correctly sits at
  the last byte of the flat binary (was 78-126 bytes short previously due to
  `.rodata` being laid out after `.data`).

### Fixed
- `str.split(sep, maxsplit)` now honours the `maxsplit` argument.
- `section .rodata` encounter-order in flat binary output: user string literals
  and float constants now fall inside `[load_addr, load_end_addr)` and are
  therefore loaded by the Multiboot1 loader.
