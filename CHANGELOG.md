# Changelog

All notable changes to asmpython are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).


## [Unreleased]

CPython-parity expansion: making common idioms compile and produce correct
output rather than silent miscompilations.

### Added

- **`io.StringIO` / `io.BytesIO` context managers**: both classes now implement
  `__enter__` / `__exit__` so they work in `with` blocks. Added `readable()`,
  `writable()`, and `seekable()` returning 1 on both classes. Added
  `io.text_open(file, mode, encoding)` as a named alternative to the builtin
  `open()` that returns a `TextIOWrapper` (the builtin `open` is unchanged).

- **`contextlib` improvements**: `suppress` is now a real class (was a stub
  function returning 0) — it implements `__enter__` / `__exit__`, suppressing
  any exception that propagates through the block (exception type filtering is
  not yet supported since class types can't be passed as arguments in asmpython).
  `nullcontext` is now a class that returns `enter_result` from `__enter__`.
  `closing.__exit__` now correctly calls `self.thing.close()`.

- **Ordering comparison dunder dispatch** (`__lt__`, `__le__`, `__gt__`,
  `__ge__`): `a < b` and friends on user class instances now call the
  corresponding dunder method (with reflected fallback, e.g. `a < b` tries
  `a.__lt__(b)` then `b.__gt__(a)`). Only single comparisons dispatch via
  dunder (chained `a < b < c` still uses integer comparison). This makes
  `Fraction.__lt__` et al. work, enabling ordered fraction comparisons.

- **`fractions.Fraction` arithmetic now works**: `+`, `-`, `*`, `/`, `**`,
  `abs()`, unary `-` and `+` all produce correct results now that binary
  and unary dunder dispatch is wired end-to-end.

- **`abs()` and `hash()` dispatch to `__abs__` / `__hash__`**: calling
  `abs(obj)` or `hash(obj)` on a user class instance now calls `__abs__`
  or `__hash__` when defined. `hash(str)` calls the same FNV-1a hasher
  used internally by the dict runtime. `abs(instance)` sets its return
  type from the `__abs__` signature so the result is correctly typed.

- **Dunder operator dispatch** (`__add__`, `__sub__`, `__mul__`,
  `__neg__`, `__pos__`, `__invert__`, and all `DUNDER_BINOP` entries):
  binary ops (`a + b`, `a * n`, etc.) and unary ops (`-a`, `+a`, `~a`)
  on user class instances now dispatch to the corresponding dunder method
  when one is defined. Sema resolves the method via the parent chain and
  stamps `dunder_owner`/`dunder_method` on the AST node; codegen emits a
  direct method call instead of the raw integer instruction.

- **`**kwargs` capture**: excess keyword arguments at call sites are now packed
  into a `dict` and passed as a trailing argument to the callee. Functions
  declared with `**kwargs` receive the overflow as a live `dict[str, any]`
  parameter — key iteration (`for k in kwargs:`), containment (`"x" in kwargs`),
  and `len(kwargs)` all work. Value subscript access (`kwargs["key"]`) returns
  `any`-typed values; typed reads require an annotated variable (`x: int = kwargs["key"]`
  once annotated assignment lands). The `kwarg` slot is represented as an
  ordinary parameter at the end of the param list so codegen requires no changes.

- **Docs restructured into `docs/` directory**: `docs/index.html` covers
  language, asmlib, assembly, targets, and reference; `docs/stdlib.html`
  covers all stdlib modules including the `asmlib.*` modules (now part of the
  standard library). The root `docs.html` redirects to `docs/index.html` for
  backwards compatibility. Cross-page nav links and per-page scroll-spy are
  fully wired.

- **`@classmethod` `cls.field` access**: reading and writing class variables
  via `cls.field` inside a `@classmethod` body now works correctly. Previously
  codegen passed `null` as `cls`, causing a segfault on any field access. Fixed
  at sema level: the `cls` parameter name is tracked while checking a classmethod
  body, and any `cls.attr` read or write is rewritten in-place to
  `ClassName.attr`, hitting the existing class-variable static-storage path.

- **Instance truthiness via `__bool__` / `__len__`**: `if obj:`, `while obj:`,
  and `not obj` now dispatch to `__bool__` (preferred) or `__len__` (fallback)
  on user-class instances. Classes with neither dunder remain unconditionally
  truthy (pointer ≠ null). Dispatches through the platform ABI just like a
  normal method call.

- **`--icon <path.ico>` CLI flag** (`--target windows` only): embeds an
  `.ico` file as the executable's Windows icon resource via `windres` from
  the gcc toolchain, so the built `.exe` shows a custom icon in Explorer and
  the taskbar. A generated `.icon.rc`/`.icon.o` are compiled and linked
  alongside the program's object file (and cleaned up unless `--keep` is
  passed); non-Windows targets print a warning and proceed without
  embedding. Verified via `objdump -h` showing a populated `.rsrc` section
  in the output binary.

- **`asmlib.gui` window-icon bindings**: `load_bmp(path)` (SDL_LoadBMP, via
  an inline `SDL_RWFromFile`/`SDL_LoadBMP_RW` wrapper since `SDL_LoadBMP` is
  a macro, not an exported symbol), `set_window_icon(win, surface)`
  (SDL_SetWindowIcon), and `free_surface(surface)` (SDL_FreeSurface), for
  both `--target linux` and `--target windows`.

- **Fixed a pre-existing FFI codegen bug**: any `asmlib.gui` call with more
  than 4 integer arguments (`create_window`, `set_draw_color`, `draw_line`,
  `fill_rect`, `draw_rect`) crashed the compiler with `IndexError` on
  `--target windows`, because Win64 has only 4 integer argument registers
  and the old FFI dispatch indexed blindly into them. `_gen_ffi_call` now
  uses the same `_assign_arg_regs`/shadow-space stack-argument machinery
  already used for user-defined function calls, correctly spilling
  positions 5+ to the stack on Windows (SysV's 6 integer registers cover all
  current FFI signatures, so Linux was unaffected). As part of this fix,
  `math.ldexp`'s Windows helper (`_math_ldexp`) was updated: the new caller
  convention places `(float, int)` args as `xmm0=x, rdx=n` — already an
  exact match for libc's `ldexp(double, int)` — so the old `mov rdx, rcx`
  shuffle (written for the previous, non-positional convention) is removed.

- **New `base64` module**: `b64encode`/`b64decode`, `standard_b64encode`/
  `standard_b64decode`, `urlsafe_b64encode`/`urlsafe_b64decode`,
  `b32encode`/`b32decode`, `b16encode`/`b16decode` (RFC 4648), all operating
  on `list[int]` (the same byte-list convention `hashlib` uses). Padding,
  the `+`/`/` vs `-`/`_` alphabets, and base32's `=`-padding table all
  verified against CPython's `base64` module, including every padding
  remainder (0-4 bytes for base32, 0-2 for base64). `a85`/`b85`
  (ascii85/base85) not implemented (rare, significantly more complex). New
  `tests/cases/172_base64_module.py`.

- **User-defined exception classes in `raise`/`except`.** `class MyError(Exception):
  pass` + `raise MyError("msg")` + `except MyError as e: print(e)` now works
  end-to-end. User exception classes deriving from any builtin exception
  (directly or transitively) already received RTTI ids and matched typed
  `except` clauses correctly; the missing piece was the `raise` codegen path:
  `raise MyError("msg")` previously called `_gen_constructor` which put an
  instance dict pointer into rax, but `_runtime_raise` expects a string.
  Fixed: for `raise UserExcClass(msg)`, codegen now evaluates the first
  argument (the message) directly, converting int/float args to string as
  needed. `raise UserExcClass()` (no args) uses the class name as the message.
  `raise UserExcClass` (bare name, no call) similarly uses the class name.
  `except MyError as e:` already stored the message string in `e` (via
  `_runtime_exc_msg`) and `scope.add(bind_name, "str")` was already correct.
  Subclass hierarchy works: `raise ParseError("bad input")` caught by
  `except AppError:` (parent), and `raise MyLookupError("key")` caught by
  `except LookupError:` (builtin parent). New test
  `tests/cases/147_user_exceptions.py` (CPython-verified): basic raise/catch,
  subclass caught by parent, exception deriving from builtin LookupError,
  `finally` running through user exception propagation.

- **`match`/`case` structural pattern matching (PEP 634).** Full `match
  subject: case pattern [if guard]: body ...` syntax with all core pattern
  forms: literal (`case 1:`, `case "x":`, `case True:`, `case None:`,
  `case -1:`), capture (`case x:` — always matches, binds subject to `x`),
  wildcard (`case _:`), or-patterns (`case p1 | p2:`, literals only —
  captures inside or-patterns are a compile error), sequence patterns
  (`case [p0, p1, *rest]:`; exact-length match without a star,
  `>= n_fixed` with a star; one `*name`/`*_` rest-capture anywhere in the
  pattern), class patterns (`case ClassName(kw=p):` and
  `case ClassName(p0, p1):` using `__match_args__`), and as-patterns
  (`case pat as name:`). Guard expressions (`case p if cond:`) are supported;
  captured names are bound before the guard is evaluated. Mapping patterns
  (`case {"key": v}:`) are not yet supported (parse error).
  `match` and `case` are soft keywords — `match = 5`, `match(x)`,
  `match.foo` all remain valid identifiers. Implemented as a pure sema
  rewrite: each `match` is lowered to a subject-temp assignment plus an
  `if`/`elif`/`else` chain before codegen, so no codegen changes were needed.
  New `_lower_pattern`, `_and_chain`, `_or_chain`, `_make_name_ref` helpers
  in `Analyzer` (`sema.py`). `_check_block` was already rewritten (WIP
  commit) to an index-based loop supporting the extra-stmt splice.
  New test `tests/cases/146_match_case.py` (CPython-verified, 13 print lines):
  literal patterns, or-patterns, captures, wildcards, sequence patterns with
  `*rest` and `*_`, class patterns (positional and keyword), guards, and
  as-patterns. New `tests/cases_fail/match_or_capture.py` for the
  or-pattern-with-capture sema error.

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
- **`collections.OrderedDict.move_to_end()` and `.popitem()`.** `move_to_end(key,
  last=True)` re-inserts the key at the end of the underlying dict (insertion
  order is preserved automatically), or rebuilds the dict with the key first
  when `last=False`. `popitem(last=True)` removes and returns the last (or
  first, with `last=False`) `(key, value)` pair, raising `KeyError` on an
  empty dict. Also fixed `OrderedDict.keys()` / `defaultdict.keys()`, which
  were declared `-> list` (opaque element type), so iterating the returned
  keys printed raw pointer values instead of the key strings; now declared
  `-> list[str]`. New `tests/cases/166_ordereddict_methods.py`
  (CPython-verified).
- **`collections.Counter` arithmetic operators: `+`, `-`, `&`, `|`.** Matches
  CPython's multiset semantics: `+` sums counts, `-` subtracts, `&` takes the
  per-key minimum, `|` takes the per-key maximum; in all four cases any key
  whose resulting count is `<= 0` is dropped from the result. New
  `tests/cases/167_counter_operators.py` (CPython-verified).
- **New `csv` module.** `reader(lines)` parses a `list[str]` of CSV records
  into `list[Row]` (`Row.fields: list[str]`), handling quoted fields,
  embedded commas, and doubled-quote (`""`) escapes exactly like CPython's
  default dialect. `writer_row(fields)` / `writer_rows(rows)` format rows
  back to CSV lines (quoting only when needed), and `DictReader` parses a
  header row plus `.get(row, name)` for named field access. Operates on
  in-memory `list[str]` rather than file objects, since asmpython has no
  file-iterator protocol to drive CPython's lazy `csv.reader(f)`. New
  `tests/cases/168_csv_module.py` (CPython-verified).
- **`asmlib.hardware.rdtsc()`, `cpuid(leaf)`, and `rdrand()` are now real on
  hosted targets (Windows/Linux), not just `--target freestanding`.** These
  three are unprivileged (ring-3) instructions, unlike the rest of
  `asmlib.hardware` (port I/O, MMIO, MSRs, control registers, PIC/PIT/
  keyboard/VGA, `halt`/`disable_interrupts`/`enable_interrupts`), which
  genuinely require ring 0 and remain safe zero-returning no-ops on hosted
  targets. New `tests/cases/169_hardware_real_ops.py`.
- **New `uuid` module.** `UUID(hex_str)` wraps a 32-hex-digit value (dashes
  optional/ignored); `.hex` is the canonical lowercase 32-digit form,
  `str(u)`/`__str__` is the dashed 8-4-4-4-12 grouping, `repr(u)`/`__repr__`
  is `UUID('...')`, and `__eq__` compares by `.hex`. `uuid4()` generates a
  random version-4 (variant 1) UUID via `random.randint`. No `uuid1`/`uuid3`/
  `uuid5` (need a MAC address / namespace hashing) and no `.bytes`/
  `.bytes_le`/`.int` (asmpython has no bytes type, and ints are 64-bit — too
  narrow for a 128-bit UUID); use `.hex`/`str(u)` instead. New
  `tests/cases/170_uuid_module.py` (CPython-verified).
- **`asmlib.hardware` gains a high-level `console_*` API** alongside its
  low-level register/port/MMIO primitives: `console_clear()`,
  `console_putc(ch)`, `console_write(s)`, `console_set_color(fg, bg)`,
  `console_set_cursor(row, col)`, and `console_get_row()`/`console_get_col()`.
  On `--target freestanding` these are thin wrappers around the VGA
  text-mode helpers `print()` already uses (writing directly into the
  0xB8000 framebuffer); on hosted Windows/Linux they drive the real terminal
  via ANSI/VT100 escapes (`ESC[2J ESC[H` to clear, `ESC[<fg>m ESC[<bg>m` for
  the 16-color VGA palette mapped to SGR/aixterm codes, `ESC[<row>;<col>H`
  for cursor positioning). Since ANSI escapes are write-only, the cursor
  position returned by `console_get_row`/`console_get_col` (0-indexed) is
  tracked internally on every target, so simple text UIs (status lines,
  menus, progress bars) can be written once and run on both bare metal and a
  normal terminal. New `tests/cases/171_hardware_console.py`.
- **Test coverage for the `atexit`, `signal`, and `subprocess` bundled
  stdlib modules.** New CPython-verified `tests/cases/298_atexit_module.py`,
  `tests/cases/299_signal_module.py`, and `tests/cases/300_subprocess_module.py`.

### Fixed

- **Division/modulo by zero now raises `ZeroDivisionError("division by zero")`**
  (matching CPython 3.13+'s unified message) instead of faulting the CPU.
  Previously `idiv` with a zero divisor executed unconditionally for `//`/`%`
  on ints (codegen's `_emit_binop_inline` and the `_runtime_divmod` helper
  used by `divmod()`), producing a `#DE` (divide error) — on the hosted
  targets this is a SIGFPE/crash, and on `--target freestanding` (no IDT
  installed) it cascades into a triple fault with no diagnostic at all. Both
  sites now check the divisor and raise via `_runtime_raise` first. Float
  `/`, `//` and `%` had a related but different gap: SSE division by zero
  doesn't fault (it produces `inf`/`nan`), so `5.0 / 0.0` silently returned
  `inf` instead of raising — new `_emit_check_float_nonzero_divisor` (called
  from `_emit_binop_inline_float`) checks the RHS against `0.0` via `ucomisd`
  and raises `ZeroDivisionError` first (NaN divisors are left alone, matching
  IEEE-754/Python semantics for `nan` operands). `_gen_binop_float` — a
  separate, duplicated float-binop implementation used for `BinOp`
  expressions — was refactored to call the shared `_emit_binop_inline_float`
  so both paths get the fix. New `tests/cases/305_zero_division.py`
  (CPython-verified: int `//`/`%`, float `/`/`//`/`%`, and `divmod()`, all
  caught as `ZeroDivisionError` with message `"division by zero"`).

- **`--target freestanding`: unhandled exceptions and panics now show a
  flashing red error screen and warm-reboot after 5 seconds**, instead of
  printing to the normal-color VGA text and halting forever (`hlt`/`jmp`
  spin loop, requiring a hard power cycle). New `_emit_set_error_color` hook
  on `Codegen` (no-op on hosted targets) is called from `_runtime_raise`'s
  unhandled-exception path; the freestanding override sets `_vga_attr` to
  `0x8C` (blinking bright red on black). `_runtime_panic` was rewritten:
  it sets the red/blink attribute, prints "KERNEL PANIC" only if
  `_runtime_exc_msg` is still unset (i.e. a direct panic from OOM or
  `os._exit`/`sys.exit`, not an unhandled `raise`), then prints "Rebooting
  in 5 seconds...", calls a new best-effort `_runtime_delay_5s` busy-wait,
  and jumps to a new `_runtime_reboot` (8042 keyboard-controller pulse-reset).
  Also fixed a **pre-existing freestanding bug where any floating-point
  instruction (`movsd`/`addsd`/`divsd`/...) triple-faulted**: kernel init
  enabled `CR4.PAE` but never set `CR4.OSFXSR`/`CR4.OSXMMEXCPT`, so SSE
  instructions raised `#UD`, which (with no IDT installed) cascaded straight
  to a triple fault. Verified under QEMU (`-serial stdio -no-reboot`): basic
  float arithmetic, an unhandled `raise`, and `1 // 0`/`1.0 / 0.0` all now
  print the expected message followed by the red-screen reboot countdown,
  while a `try`/`except ZeroDivisionError` around the same division still
  prints "caught"/the message/"after" normally (handler path unaffected).
- **`except module.ExceptionClass as e:` (a dotted exception type) now
  parses and matches correctly**, instead of "'except' type must be a name
  or a tuple of names". asmpython's whole-program merge keeps a single flat
  class namespace, so `_parse_try` now accepts an `A.Attr` (dotted name)
  expression — alone or inside an `except (a.B, C) as e:` tuple — and uses
  just the final component (`ExceptionClass`/`B`) as the match name, the
  same name the class is registered under regardless of which module
  defines it. New `tests/cases/304_dotted_except_type.py`
  (`except subprocess.CalledProcessError as e:` and a mixed
  `(subprocess.CalledProcessError, ValueError)` tuple).
- **Quoted forward-reference annotations (PEP 484), e.g. `def parent(self) ->
  "Path": ...` or `def f() -> "list[int]": ...`, now resolve to the real
  type instead of degrading to unconstrained `any`.** `_parse_annot_unit`
  used to consume a `STRING` annotation token and return `("any", None)`
  outright ("re-lex isn't worth it"); it now re-lexes the string's contents
  with a fresh `Lexer`/`Parser` and parses them as a normal annotation via
  the new `_parse_annot_from_string` (falling back to `any` only if the
  contents aren't a parseable type expression). This makes genuine
  forward references to a class defined later in the same file resolve
  correctly (sema's two-pass class registration already supports this for
  bare names; only the quoting broke it). New
  `tests/cases/303_quoted_forward_ref.py` (CPython-verified).
- **`ospath.isdir`/`ospath.isfile` (and `pathlib.Path.is_dir`/`.is_file`) were
  wrong on Windows: `isdir` always returned `1` for any existing path
  (including regular files), so `isfile` always returned `0`.** Root cause:
  they used `os._opendir`/`os._closedir` (MinGW `opendir()`), which returns a
  non-NULL `DIR*` for regular files too, without checking `S_ISDIR`. Rewrote
  both to call `os._stat` and inspect the `st_mode` file-type bits directly:
  new `os.S_IFMT`/`S_IFDIR`/`S_IFREG` constants and `os._ST_MODE_WORD` (the
  word index of `st_mode` within the `_stat` buffer — word 3 on Linux's
  glibc `struct stat`, the high 16 bits of word 0 on Windows' MinGW
  `struct _stat64`, offsets verified against the bundled MinGW headers).
  `pathlib.Path.is_dir`/`.is_file` now delegate to `ospath.isdir`/`isfile`.
  New `tests/cases/301_ospath_isdir_isfile.py` and
  `tests/cases/302_pathlib_isdir_isfile.py` (CPython-verified).
- **Float-default arguments (`def f(x: float = 0.0):`) now parse correctly**
  instead of raising "float default arguments aren't supported yet".
  `_parse_default_literal` gained an `A.FloatLit` case alongside its existing
  int/str/bool/None cases, so float defaults flow through the same
  substitution path as explicit float call arguments. This unblocks
  `signal.setitimer(which, seconds, interval: float = 0.0)` and similar
  bundled-stdlib signatures.
- **Platform-conditional top-level constants (e.g. `signal.py`'s
  `if sys.platform == "win32": SIGABRT: int = 22 ... else: SIGABRT: int = 6`)
  are now visible as module attributes (`signal.SIGABRT`, `signal.NSIG`,
  etc.) instead of reading back as `0`.** Two parts: `program.py`'s
  `_merge_import_bindings` now hoists "simple constant if/else" blocks of
  top-level assigns into the entry module's body (new
  `_simple_const_if_targets` helper), and codegen's `global_vars` collection
  (used by module-attribute reads) now recurses into top-level `if`/`elif`/
  `else` chains via a new `_collect_if_globals` method.
- **`raise UserExcClass(n)` where `n` is an `int`/`float` no longer fails to
  assemble on the Windows and Linux targets** (symbol `_runtime_int_to_str`
  not defined). The `raise` codegen path now calls the target-agnostic
  `_emit_int_to_str()`/`_emit_float_to_str()` helpers instead of raw
  freestanding-only runtime labels.
- **`subprocess.getstatusoutput` now returns a `tuple[int, str]`** (matching
  CPython's `(status, output)`), instead of a `list` containing mixed `int`
  and `str` elements (a combination sema rejects as "mixed list element
  types").
- **`docs.html`** now points at the project's actual repository,
  `https://github.com/deltathedumb/asmpython`, instead of a stale clone URL.

- **`-> list[tuple[T1, T2]]` annotations lost the per-slot element kinds,
  so `for a, b in f()` (where `f` returns `list[tuple[str, int]]`) typed
  both unpack targets as `"any"` and printed the `str` slot as a raw
  pointer value instead of the string.** The parser's `_normalize_annot`
  collapsed `list[tuple[str, int]]` to plain `("list", "tuple")`, discarding
  `["str", "int"]`; now it keeps `("list", ("tuple", ["str", "int"]))`, sema's
  `_resolve_annot` resolves those slot kinds into a new `FuncSig.
  ret_list_tuple_types`, and call sites (`A.Call` and instance `A.MethodCall`)
  stamp `tuple_elem_types` on the call node so `_list_el_tuple_types` (used by
  `for a, b in <list[tuple]>` unpacking and `xs[i][j]` indexing) sees the real
  shape. This unblocked rewriting `collections.Counter.most_common()` (see
  below) to return real tuples instead of a wrapper class.

- **`collections.Counter.most_common()` now returns `list[tuple[str, int]]`**
  (previously `list[CountPair]`, a wrapper class with `.element`/`.count`
  fields), matching CPython exactly. `for el, cnt in c.most_common(): ...` and
  `c.most_common(2)[0][0]` now work as in real Python. `CountPair` removed
  (no longer needed). `tests/cases/151_collections_module.py` extended to
  cover both access patterns.

- **`for a, b in <list[T]>` where `T` is a plain user class (not a tuple)
  segfaulted (exit 139) instead of raising a compile error.** Multi-target
  `for`-loop unpacking over a list assumed every element was itself a
  list/tuple buffer (`_gen_for_list` dereferenced `[element + LIST_BUF_OFF]`
  to get the per-slot values); for `list[Pair]` the element is a pointer
  directly to a `Pair` instance, so this read and dereferenced a garbage
  "buffer pointer" from inside the instance's fields. CPython rejects this at
  runtime with `TypeError: cannot unpack non-iterable Pair object`; sema now
  raises the same message at compile time when a `list[T]` element type `T`
  is a plain class. New `tests/cases_fail/for_unpack_non_iterable_instance.py`.

- **`print(0.0)` on Windows printed `inf` instead of `0.0`.** The Windows
  NaN/inf pre-check in `_emit_float_to_str` compared `abs(bits) ==
  0x7FF0000000000000` with `cmp rax, <64-bit immediate>`, but `cmp r64, imm`
  only takes a sign-extended 32-bit immediate -- NASM silently truncated the
  inf-bit-pattern immediate to `0`, so `0.0` (whose abs bits are also `0`)
  matched the "is infinity" branch. Fixed by loading the inf bit pattern into
  a register (`mov r10, 0x7FF0000000000000` / `cmp rax, r10`) before
  comparing.

- **Adding a `float` to an element read out of an unannotated `list`
  produced wrong results** (e.g. `statistics.mean`/`variance` over
  `list`-typed data returned `0.0`). A bare `list` parameter/local has
  element type `"any"` (unknown).
  sema's `BinOp` type check short-circuited `float + any` (and `any + float`)
  to result type `"any"` instead of applying normal numeric promotion to
  `"float"`; codegen's assignment/return paths then mis-dispatched the
  `addsd` result back through an extra `cvtsi2sd`, clobbering it. Now `<op>`
  between a `"float"` operand and an `"any"` operand (for non-bitwise ops)
  is typed `"float"`, matching `A.expr_type`'s general numeric-promotion
  fallback.

- **`return <int-or-any-typed expr>` from a `-> float` function didn't
  convert to a double**, so e.g. `def median(data: list) -> float: return
  sorted_data[n // 2]` returned a raw integer in `rax` while the caller read
  `xmm0`, printing `0.0` (or garbage). `FuncInfo` now carries `ret_is_float`
  (from the function's `-> float` annotation), and `return` promotes a
  non-float result via `cvtsi2sd` when the declared return type is `float`.

- **`asmpython/stdlib/textwrap.py`**: `wrap()`/`_split_words()`/
  `_split_lines()`/`TextWrapper.wrap()` were annotated `-> list` (element
  type `"any"`), so `print(wrap(...)[0])` printed a raw pointer instead of
  the string. Now annotated `-> list[str]`.

- Corrected several `# expect:` blocks in `tests/cases/` (146_match_case,
  162_heapq_module, 163_bisect_module, 164_statistics_module,
  97_import_sys) that didn't match either CPython's actual output or the
  current `sys.version` string -- the implementations were already correct;
  only the test expectations were stale/typo'd.

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
- **`repr(x)` on a user class instance printed a raw pointer value instead of
  calling `__repr__`/`__str__`.** The `repr()` builtin's codegen had no
  `instance:` branch (unlike `str()`, which already dispatched via
  `_resolve_str_dunder`); it fell through to `_emit_int_to_str()`, printing
  the instance's heap address. Now `repr()` resolves `__repr__` (falling back
  to `__str__`) via the existing `_resolve_repr_dunder` helper, mirroring
  `str()`'s dispatch. Found while testing the new `uuid` module
  (`repr(UUID(...))`).
- **`a == b` / `a != b` between two instances of a class defining `__eq__`
  did raw pointer comparison instead of calling `__eq__`.** `Compare` had no
  dunder-dispatch path for `==`/`!=` (unlike arithmetic operators, which
  already resolve `__add__`/`__radd__` etc. via `DUNDER_BINOP`) — two
  equal-by-value but distinct objects (e.g. `UUID(hex_str)` constructed twice
  with the same hex) compared `==` as `False`. Now, for a single
  (non-chained) `==`/`!=` comparison where either operand is a user instance,
  sema resolves `__eq__` (mirroring `DUNDER_BINOP`'s resolution) and stamps
  `dunder_owner`/`dunder_method`/`dunder_negate` on the node; codegen calls it
  (parking `self` in a new `__cmpeq_lhs_*` scratch slot across the other
  operand's evaluation, like `_gen_binop`'s `__binop_lhs_*`) and negates the
  result for `!=` (CPython's default `__ne__` is `not __eq__`). Found while
  testing the new `uuid` module (`UUID(a) == UUID(b)` with equal hex values).

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
