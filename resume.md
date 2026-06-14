# Resume notes — autonomous CPython-parity loop (parity-expansion branch)

## PAUSED MID-TASK (2026-06-14) — resume here first

Work is paused mid-implementation of `match`/`case` (structural pattern
matching), the second of two features requested this session (multiple
context managers in one `with` was the first — DONE, committed `a7e619c`).
**The repo is in a safe, additive state** — no existing tests are broken by
the partial work (verify with `py -m tests.runner` before continuing, but it
should still be 189/189 since `A.Match` is never produced unless `match`/`case`
syntax is actually parsed, which no existing test does).

### match/case: what's DONE so far

1. `asmpython/_compiler/ast_nodes.py` — added new pattern AST nodes (after the
   `Del` class, before the `Stmt` union): `MatchValue`, `MatchCapture`,
   `MatchOr`, `MatchSequence`, `MatchClass`, `MatchAs`, the `Pattern` union,
   and `Match` (the statement node: `subject: Expr`, `cases: list[(Pattern,
   Optional[Expr] guard, list[Stmt] body)]`). `Match` added to the `Stmt`
   union.
2. `asmpython/_compiler/parser.py` — full parser support added (after
   `_parse_with`, before `_parse_raise`):
   - `_looks_like_match_stmt()`: soft-keyword lookahead (save `self.i`, eat
     `match`, speculatively `_parse_expr()`, check for `: NEWLINE`, restore).
   - Hooked into `_parse_stmt` right before the "Assignment / aug-assignment
     vs expression statement" NAME-led block: `if t.kind == "NAME" and
     t.value == "match" and self._looks_like_match_stmt(): return
     self._parse_match()`.
   - `_parse_match()`, `_parse_case()` (soft keyword `case` via
     `_expect("NAME", "case")`), `_parse_case_pattern()` (handles
     unparenthesized sequence patterns `case a, b:` / `case a, *rest:`),
     `_parse_star_pattern()`, `_parse_as_pattern()` (`pattern as name`),
     `_parse_or_pattern()` (`p1 | p2`), `_parse_sequence_items()`,
     `_parse_closed_pattern()` (literals incl. negative numbers,
     `True`/`False`/`None`, `_` wildcard, capture names, dotted value
     patterns `Color.RED`, `(...)`/`[...]` sequences, `{...}` raises
     ParseError "mapping patterns ... are not supported"), and
     `_parse_class_pattern()` (`ClassName(p0, kw=pk)`, dotted class names use
     the last segment).
3. `asmpython/_compiler/sema.py` — `_check_block` rewritten to an index-based
   loop that splices extra already-checked statements returned by
   `_check_stmt` immediately before the current statement:
   ```python
   def _check_block(self, stmts: list, scope: Scope) -> None:
       i = 0
       while i < len(stmts):
           s = stmts[i]
           extra = self._check_stmt(s, scope)
           if extra:
               stmts[i:i] = extra
               i += len(extra)
           i += 1
   ```
   `_check_stmt`'s signature changed to `-> "Optional[list]"`. All existing
   branches still implicitly `return None` (unchanged).

### match/case: what's NOT done yet (the actual remaining work)

**Nothing in sema handles `A.Match` yet** — this is the big remaining piece.
Need to add, in `_check_stmt` (suggested location: right after the
`isinstance(s, A.With)` branch, which ends around sema.py:2577 — search for
`if isinstance(s, A.MultiAssign):` and insert before it):

```python
if isinstance(s, A.Match):
    subject_name = f"__match_subject_{id(s)}"
    subject_assign = A.Assign(target=subject_name, value=s.subject, pos=s.pos)
    self._check_stmt(subject_assign, scope)

    def ref_factory(pos=s.pos, _name=subject_name):
        return A.Name(name=_name, pos=pos)

    orelse: list = []
    for pattern, guard, body in reversed(s.cases):
        test, binds = self._lower_pattern(pattern, ref_factory, scope)
        if guard is not None:
            test = A.BoolOp(op="and", left=test, right=guard, pos=s.pos)
        if_node = A.If(test=test, then=binds + list(body), orelse=orelse, pos=s.pos)
        orelse = [if_node]

    top = orelse[0]
    s.__class__ = A.If
    s.test = top.test
    s.then = top.then
    s.orelse = top.orelse
    self._check_stmt(s, scope)
    return [subject_assign]
```
(NB: no closures allowed if this needs to stay self-host-compilable later —
but sema.py is host-Python only for now, like the rest of the compiler, so a
nested `def ref_factory` is fine here, matching existing style elsewhere in
sema.py? **Double check**: grep sema.py for existing nested-def closures to
confirm the convention before relying on this — if none exist, prefer a small
helper method `_const_name_ref(name, pos)` returning a lambda-free factory, or
just inline-build a tiny `(name, pos)`-keyed callable class. This is a style
nit, not a correctness blocker.)

Then add `_lower_pattern(self, pattern, ref_factory, scope) -> tuple[A.Expr,
list]` (returns `(test_expr, bind_stmts)`), plus small `_and_chain`/`_or_chain`
helpers (left-fold `A.BoolOp`). Cases to implement:

- **MatchValue(value)**: `test = A.Compare(ops=["=="], operands=[ref_factory(),
  pattern.value], pos=pattern.pos)`, `binds = []`.
- **MatchCapture(name)**: if `name == "_"`: `test = A.IntLit(value=1,
  is_bool=True, pos=...)`, `binds=[]`. Else same test, `binds =
  [A.Assign(target=name, value=ref_factory(), pos=...)]`.
- **MatchOr(patterns)**: reject (SemaError) if any alt contains a non-wildcard
  `MatchCapture` (no binding allowed in or-patterns, documented scope
  limitation). `test = _or_chain([_lower_pattern(p, ref_factory, scope)[0] for
  p in patterns])`, `binds=[]`.
- **MatchSequence(patterns, star_index)**: see full design in the prior
  session summary (length test via `len()` + `==`/`>=`, per-element
  `Subscript(ref_factory(), IntLit(i))` for fixed-from-start, `Subscript(...,
  BinOp("-", Call("len",[ref]), IntLit(offset)))` for fixed-from-end, star
  capture binds a `Slice` sub-list). AND-chain all sub-tests + length test.
- **MatchClass(cls_name, positional, kwargs)**: `test` starts with
  `A.Call(func="isinstance", args=[ref_factory(), A.Name(name=cls_name,
  pos=...)], pos=...)`. For `positional`, resolve `__match_args__` from the
  class's `class_vars` (search `self.mod.classes` for `c.name == cls_name`,
  find a class_var named `"__match_args__"` whose value is a
  `TupleLit`/`ListLit` of `StrLit`s) — SemaError if positional patterns given
  but no/insufficient `__match_args__`. Each positional/kwarg sub-pattern's
  ref is `A.Attr(obj=ref_factory(), name=attr_name, pos=...)`. AND-chain.
- **MatchAs(pattern, name)**: `test, binds = (IntLit(1,is_bool=True), []) if
  pattern is None else _lower_pattern(pattern, ref_factory, scope)`; if `name`
  append `A.Assign(target=name, value=ref_factory(), pos=...)` to `binds`.

**IMPORTANT — fresh nodes only**: every call to `ref_factory()` (and any
nested ref-factory built from it) must return a *freshly constructed* node
tree (mirrors the `with`-rewrite's `A.Name(name=cm_name, pos=s.pos)` being
built fresh at each of its two use sites) — do NOT reuse the same node object
in two places in the rewritten tree.

### After the rewrite works

1. Write `tests/cases/146_match_case.py` (check next available number — 145
   was the last used by `145_multi_with.py`), CPython-verified, covering:
   literal patterns (incl. negative numbers, `None`/`True`/`False`, strings),
   capture + wildcard, or-patterns, guards (`case p if cond:`), sequence
   patterns with `*rest`, class patterns (kwargs + `__match_args__`
   positional). Maybe a `tests/cases_fail/*.py` for the mapping-pattern
   ParseError or the or-pattern-with-capture SemaError.
2. `py -m tests.runner` -> confirm 189/189 + new test(s) green (190+/190+).
3. `docs.html`: new "Pattern matching (`match`/`case`)" section; remove
   `match`/`case` from the limitations table; document the mapping-pattern gap
   and the or-pattern-no-capture limitation.
4. `CHANGELOG.md` `[Unreleased]`: add entry.
5. Commit (`git commit`), then **push** (`git push`, see new directive below —
   the user asked to push regularly this session; this branch is currently 34
   commits ahead of `origin/parity-expansion` and none of this session's
   commits have been pushed yet — push everything, not just the new commit,
   once things are green. Confirm before any force-push, but a plain `git
   push` on `parity-expansion` should be safe/expected per the new directive).
6. Update this resume.md: bump feature count, add commit hash + "what changed"
   section for match/case, update test count, update "Next steps".

## NEW STANDING DIRECTIVES added this session (after match/case is done)

The user interrupted the match/case work with several additional directives
that apply to *all* future work in this loop, not just match/case:

- **"push all changes regularly too"** — after each commit (per the existing
  "commit at checkpoints" directive), also `git push` to
  `origin/parity-expansion`. 34 local commits are currently unpushed as of
  2026-06-14 (this is a backlog — push these too, after confirming nothing is
  broken).
- **"i want breadth"** + **"note that the stdlib and asmlib and especially
  asmlib.hardware libraries all need to be fully production ready and equal to
  python"** — in response to a (somewhat stale-looking) review giving the
  branch "7.5/10" parity and saying *"The fixes are focused on correctness and
  debuggability rather than breadth"*. The user wants the **next phase of
  work** to pivot from deep single-feature polish (format specs, dict
  ordering, etc.) toward **breadth**: covering more of CPython's stdlib
  surface (`collections`, `itertools`, `functools`, `re`, `json`, `pathlib`,
  `os`, etc. — see the review's "Still Missing: Standard library" list below)
  AND making `asmlib`/`asmlib.hardware` (asmpython's own assembly-package
  stdlib for hardware/systems programming) "fully production ready".
  - **This is a large, multi-session undertaking.** Do NOT try to do it all at
    once. After match/case lands, the next step is a **survey task**: read
    `asmpython/stdlib/` (and wherever `asmlib`/`asmlib.hardware` actually live
    — grep for `asmlib` and `hardware` to find the package(s); the
    `.asmpkg`/`pkgformat` machinery mentioned in sema.py's `include()` handling
    is probably relevant) to inventory what currently exists vs. what CPython
    provides, then write a prioritized breadth plan (probably as a new
    resume.md section or a separate planning doc) before writing any code.
  - Still apply the "go full, not minimal" and "extend, don't edit for
    compat" standing directives to whatever stdlib modules get tackled.
  - The review's "Still Missing" list (context for prioritization — not
    necessarily all in-scope, especially generators/async which look
    explicitly out-of-scope per earlier notes):
    - Language: generators/`yield`, multiple inheritance, `async`/`await`,
      descriptors/metaclasses, `*args`-unpacking-at-call-sites for non-tuple
      sources, `match`/`case` (in progress now).
    - Stdlib: `collections`, `itertools`, `functools`, `pathlib`, `json`,
      `csv`, `re`, file I/O beyond raw `os` calls, `unittest`/`pytest`.
  - Note: this review appears to predate `with`/`__enter__`/`__exit__`,
    `@property` (full, not just getters), multi-`with`, and the
    unannotated-param-inference work already landed this session — so its
    "Still Missing" list may already be partially stale. Verify against
    current `docs.html` limitations table before trusting it.

## Status as of 2026-06-14

Eighteen features landed and committed this session (most recent last):

- `112930a` — bool/None values print/format as `True`/`False`/`None`
  everywhere (print/str/repr/f-strings/type()).
- `ccd8c4a` — walrus operator `:=` (PEP 572 assignment expressions),
  including correct comprehension-scope leaking via `_merge_walrus_bindings`.
- `101e68a` — `enumerate(iterable, start)`: optional 2nd arg sets the index
  variable's starting value.
- `448fe7e` — starred assignment unpacking `a, *rest = xs` / `*init, last = xs`
  / `first, *mid, last = xs` (PEP 3132). New `A.StarTarget` AST node.
- `32762bd` — try/except multi-handler type dispatch fix (RTTI-based,
  details below).
- `8e1ae65` — dict union operators `d1 | d2` / `d1 |= d2` (PEP 584)
  (details below).
- `8066154` — dict literal unpacking `{**d1, "k": v, **d2}` (PEP 448)
  (details below).
- `86977c0` — **dicts now iterate in insertion order, matching CPython 3.7+**
  (details below).
- `ee50017` — f-string format-spec alignment/fill/width
  (`[[fill]align]width`) for str/int/float/bool, and a fix for
  `print(f"...")` bypassing format specs entirely (details below).
- `6b2f8a8` — f-string binary format spec `b`/`#b` for ints, with
  zero-pad width and alignment overrides (details below).
- `82ea27b` — f-string grouping options `,`/`_` (PEP 378/515 thousands
  separators) for int/float, with a documented zero-pad+grouping gap
  (details below).
- `91eeb9f` — f-string `.precision` truncation for `str` segments
  (`f"{name:.5}"`), combinable with alignment/width and `s` (details below).
- `754a61f` — `str.format()` now supports the full format-spec
  mini-language and `!r`/`!s`/`!a` conversions, via the same
  `_gen_fstring_segment` dispatcher as f-strings (details below).
- `6cdb5c6` — `@property` setters (`@x.setter`): `obj.x = value` dispatches
  to the setter via the existing method-call machinery (details below).
- `22cc4b9` — f-string/`.format()` zero-pad width + grouping combo
  (`f"{n:015,}"` -> `"000,001,234,567"`), resolving the gap documented in
  the 82ea27b section (details below).
- `aa44d13` — `str.format()` named fields (`"{name}".format(name="bob")`),
  resolving part of the gap documented in the 754a61f section (details
  below).
- `e75cdd5` — unannotated parameter/return type inference from call-site
  arguments (details below). Also includes the `with`/`returns_self` sema
  changes for the next entry (both were uncommitted in the same working tree
  when this commit was made).
- `58fd662` — `with`/`__enter__`/`__exit__` context managers (details below).

189/189 tests passing. Working tree clean except this `resume.md` (untracked
scratch file, not part of the repo's feature work — don't worry about
committing it).

## f-string format-spec alignment/fill/width (ee50017) — what changed

Implements the `[[fill]align]width` prefix of Python's format-spec
mini-language for f-string segments, e.g. `f"{name:>10}"`, `f"{name:*^11}"`,
`f"{n:0>5}"`, combinable with numeric specs like `f"{pi:>10.2f}"`.

- New `codegen.py` helpers: `_split_fmt_align(spec)` splits an optional
  `[[fill]align]` prefix (`<>^=`) off the front of a spec, returning
  `(fill_char, align_char_or_None, rest)`. `_split_fmt_width(body, t)` then
  splits a leading width off `body` — for `str` it expects only digits
  (optionally trailing `s`); for `int`/`float` it preserves any
  sign/`#`/zero-pad prefix in `rest` (dropping the `0` zero-pad flag itself,
  since alignment now handles fill explicitly).
- New `_gen_fstring_aligned(seg, info, t, conv, width, fill, align, rest)`:
  evaluates `seg` to its unpadded string form (via the same str/float/int
  conversion logic as the non-aligned path — including the cfmt-spec path
  for numeric `rest`), then pads/justifies via the pre-existing
  `_runtime_str_{ljust,rjust,center}` runtime helpers (which always produce
  a safe new allocation, even when `width <= len`).
- `_gen_fstring_segment` rewritten: parses `[[fill]align]width` via the two
  new helpers; if an align char is present (explicit or, for `str`, implied
  by a bare width defaulting to `<`), dispatches to `_gen_fstring_aligned`.
  Otherwise falls back to the pre-existing numeric-cfmt / plain-conversion
  paths (now using `_cfmt_for_spec(body, t)` — `body` has only the
  `[[fill]align]` prefix stripped — which also fixes the pre-existing
  `=`-align case producing an invalid `%=010lld` printf format).
- **Key correctness fact**: a *non-empty* format spec on a `bool` formats
  the underlying `int` (0/1) — `bool.__format__` is inherited from `int` and
  has no override. `f"{True:>8}"` -> `"       1"`, NOT `"    True"`. Only the
  no-spec case (`f"{True}"`) renders `"True"`/`"False"`. `_gen_fstring_aligned`
  does NOT special-case bool/None — it falls through to the plain-int path
  for any `t` that isn't `str`/`float`.
- **Found and fixed a second, separate bug**: `print(f"...")` does NOT route
  through `_gen_fstring`/`_gen_fstring_segment` at all — `_gen_print` (for an
  `A.FString` arg) calls `_emit_print_value(seg, info)` per segment, which
  has its own early-exit condition for routing through the segment
  formatter. That condition was `(spec and t in ("int","float")) or (conv and
  ...)` — so a `str` segment with a format spec (e.g. `{name:>10}`) fell
  through to a bare `gen_expr` + `printf("%s",...)`, silently ignoring the
  spec entirely. Fixed by adding `"str"` to that tuple:
  `(spec and t in ("int","float","str")) or (conv and ...)`.
- New test `tests/cases/135_fstring_align.py` (CPython-verified, 20 print
  lines): str align `<`/`>`/`^` with default and custom fill chars, bare
  width on str (defaults to left-align), int align with `0`-fill and
  `*`-fill, negative ints, float align combined with `.2f` precision, and
  the bool-as-int-when-spec'd case.

## f-string binary format spec b/#b (6b2f8a8) — what changed

Implements the `b`/`#b` format type for `int` f-string segments, e.g.
`f"{42:b}"` -> `"101010"`, `f"{42:#b}"` -> `"0b101010"`, with optional
zero-pad width (`f"{42:08b}"` -> `"00101010"`, `f"{-5:#010b}"` ->
`"-0b0000101"` — sign and `0b` prefix count toward the requested width).

- New `_runtime_int_to_binary` runtime helper (`rax`=n, `rbx`=min total
  width, `rcx`=prefix flag) -> fresh-allocated binary string. Computes
  `avail = max(0, width - sign - prefix*2)` minimum digit count, zero-pads,
  prepends `"0b"` if requested, prepends `"-"` if negative.
- New `_parse_binary_spec(body)` parses trailing `b`/`#b`/`0Nb`/`#0Nb` ->
  `(min_total_width, prefix_flag)` or `None`.
- New `_emit_int_to_binary_str(width, prefix_flag)` emits the
  width/prefix-flag setup + `call _runtime_int_to_binary`.
- New `_gen_int_value_str(seg, info, rest)` factors the int-value formatting
  (binary / cfmt / decimal) shared between `_gen_fstring_aligned` and the
  non-aligned path in `_gen_fstring_segment`.
- Both `135_fstring_align`-style alignment overrides (`f"{n:*>10b}"`) and
  zero-pad width work correctly together: explicit alignment chars override
  zero-pad (digits unpadded, then justified with the given fill).
- New test `tests/cases/136_fstring_binary.py` (CPython-verified, 15 print
  lines): `b`/`#b`/zero-pad combos for positive/negative/zero, plus
  `*>10b`/`*<10b`/`*^12b` alignment overrides.

## f-string grouping options ,/_ (82ea27b) — what changed

Implements the `,`/`_` grouping options (PEP 378/515 thousands separators)
for `int`/`float` f-string segments, e.g. `f"{1234567:,}"` -> `"1,234,567"`,
`f"{1234567:_}"` -> `"1_234_567"`, `f"{amount:,.2f}"` -> `"1,234,567.89"`,
combinable with `d` and with alignment/width (`f"{n:>15,}"`).

- New `_strip_grouping_option(spec)` removes a `,`/`_` char and returns
  `(sep_char_or_None, spec_without_it)`. Must run *before*
  `_cfmt_for_spec`, since C printf has no equivalent of a literal `,` in a
  format string (`"%,lld"`/`"%,.2f"` would be invalid).
- New `_emit_group_digits(sep)` calls `_runtime_group_digits` as a
  post-processing step on the formatted numeric string (fresh allocation).
- New `_runtime_group_digits` runtime helper (`rax`=numeric string,
  `rbx`=separator byte) inserts `sep` every 3 digits in the integer part —
  after any leading `-`, before any `.` fraction — and copies the fraction
  verbatim.
- `_gen_int_value_str`, `_gen_fstring_aligned`'s float branch, and
  `_gen_fstring_segment`'s non-aligned numeric path all strip the grouping
  option first, format as before, then apply `_emit_group_digits` if
  present.
- **Known gap (documented in docs.html)**: combining a grouping option with
  a zero-padded width (`f"{n:015,}"`) zero-pads to the requested digit count
  but *omits* the separators (`"000000001234567"`), unlike CPython's
  separator-aware zero-padding (`"000,001,234,567"`). `_strip_grouping_option`
  detects this combination (`rest[0]=="0"` and a digit follows) and drops the
  grouping option entirely rather than producing the wrong total width.
- New test `tests/cases/137_fstring_grouping.py` (CPython-verified, 10 print
  lines): basic `,`/`_` grouping, `,d`, float `,.2f` (positive/negative),
  small numbers (no separator needed), zero, and grouping combined with
  alignment/width (`f"{n:>15,}"`).

## f-string .precision truncation for str (91eeb9f) — what changed

Implements `.precision` on `str` f-string segments, e.g. `f"{name:.5}"` ->
first 5 chars (no-op if shorter), combinable with alignment/width and `s`
(`f"{name:>10.5}"`, `f"{name:10.5s}"`).

- New `_runtime_str_truncate` runtime helper (`rax`=s, `rbx`=max n) -> fresh
  allocation of `min(len(s), n)` bytes + NUL.
- New `_split_str_width_precision(body)` parses `[width][.precision][s]` for
  `str` specs; returns `(None, None)` if `body` doesn't fully match (falls
  back to unmodified, same as the pre-existing unsupported-spec behavior).
- `_gen_fstring_aligned` gained an optional `precision` param: for `t ==
  "str"`, truncates (after any `!r`/`!a` conversion) before
  ljust/rjust/center padding.
- `_gen_fstring_segment`: for `t == "str"`, always calls
  `_split_str_width_precision` (replacing the old `_split_fmt_width` call);
  if `align is None and width is not None` defaults to left-align (as
  before); a new precision-only branch (no align/width) truncates and
  returns directly.
- New test `tests/cases/138_fstring_precision.py` (CPython-verified, 8 print
  lines): plain `.5` truncation, no-op when string is shorter, combined with
  `>`/bare-width(default `<`)/`^` alignment and custom fill, `.0` (truncates
  to empty), and `.5s`.

## str.format() format-spec mini-language (754a61f) — what changed

`"...".format(args)` (literal format string only) now supports the same
`!r`/`!s`/`!a` conversions and `:`format-spec mini-language as f-strings:
`"{:>10}".format(name)`, `"{0:.2f}".format(pi)`, `"{:08b}".format(n)`,
`"{:,}".format(1234567)`, `"{!r}".format(name)`, `"{0!r:>8}".format(name)`.

- `_gen_str_format`'s field parser now splits each `{field}` into
  `(idx, spec, conv)`: `name_conv, _, spec = field.partition(":")`, then
  `idx_part, conv = name_conv.split("!", 1)` if `"!"` is present. Previously
  `idx_part = field.split(":", 1)[0].strip()` would crash on `int("0!r")` for
  any field with a `!conv` and silently dropped any `:spec`.
- `emit_piece` for `("arg", idx, spec, conv)` pieces stamps `fmt_spec`/
  `conv_flag` onto `e.args[idx]` (the same attributes the f-string parser
  sets on segment expressions) immediately before calling
  `_gen_fstring_segment` — so all the format-spec codegen (alignment,
  binary, grouping, precision) is shared automatically, with no new runtime
  helpers needed. Safe for repeated-index fields with different specs
  (`"{0:>10} {0:.2f}"`) since each piece is fully emitted before the next
  piece's attrs are stamped.
- New test `tests/cases/139_str_format_spec.py` (CPython-verified, 10 print
  lines): auto/explicit index, alignment, float precision, binary, grouping,
  `!r`, `!r` + alignment, str `.precision`, multi-arg reordering, and `*`-fill
  centering.

## @property setters (6cdb5c6) — what changed

`@x.setter`-decorated methods now work: `obj.x = value` dispatches to the
setter instead of raising `property 'x' of 'C' object has no setter`.
Getter/setter pairs participate in inheritance and virtual dispatch like any
other method.

- parser (`_eat_decorators`): detects the `@<name>.setter`/`.getter`/
  `.deleter` dotted-accessor pattern and captures it as a single decorator
  string `"x.setter"` (previously only the leading `x` was captured,
  indistinguishable from a plain `@x` decorator).
- sema `ClassSig`: new `setters: dict[str, str]` maps property name -> the
  setter's *mangled* method name (`"x" -> "x__setter"`). In the class-sig
  building loop, a method `m` whose decorators include `f"{m.name}.setter"`
  has `m.name` renamed in place to `f"{m.name}__setter"` *before* being
  stored in `sig.methods` — this is what makes `_method_symbol(cls, "x")`
  (the getter) and `_method_symbol(cls, "x__setter")` (the setter) distinct
  linker symbols, avoiding a collision when both are named "x" in Python.
  `sig.setters["x"] = "x__setter"` records the mapping.
- new `_resolve_setter(class_name, prop_name)` (sema.py, next to
  `_resolve_method`): walks the parent chain looking up `cls.setters`,
  returns the mangled setter name or `None`.
- `A.AttrAssign` check (sema.py, `_check_stmt`): when the target is `obj.x`
  and `x` resolves to an `@property` getter, look up `_resolve_setter`. If
  found, rewrite `s` *in place* — same pattern as the pre-existing Attr ->
  MethodCall property-getter rewrite — `s.__class__ = A.ExprStmt`,
  `s.expr = A.MethodCall(obj=s.obj, method=setter_name, args=[s.value])`,
  then `self._check_expr(s.expr, scope)` (which runs the normal
  instance-method-call checks: arity, arg type-checking via
  `_maybe_bind_method_args`, virtual-dispatch resolution). If no setter is
  found, the original "has no setter" `SemaError` still fires.
- **No codegen changes needed at all** — because the setter is registered in
  `sig.methods` under its mangled name like any other method, `_resolve_method`/
  `_resolve_method_owner`/`_virtual_dispatch_rows`/`_cl_walk_expr`/
  `_gen_method_call` all handle the rewritten `MethodCall` node unchanged.
- New test `tests/cases/140_property_setter.py` (CPython-verified): a
  `Temperature` class with `celsius`/`fahrenheit` getter+setter pairs that
  share underlying state (round-trip through both), and an `Animal`/`Dog`
  subclass inheriting an `@property`/`@x.setter` pair from the base class.

## Zero-pad+grouping combo (22cc4b9) — what changed

Implements CPython's separator-aware zero-padding for the combination of a
zero-padded width and a `,`/`_` grouping option, e.g. `f"{1234567:015,}"` ->
`"000,001,234,567"` (previously dropped the separators and produced
`"000000001234567"`, see 82ea27b section above for the prior gap).

- `_strip_grouping_option(spec)` simplified: no longer special-cases the
  zero-pad-width combo by dropping the separator — always returns `(sep,
  rest)` when `,`/`_` is found, leaving `rest` (including any `0` zero-pad
  flag + width) for `_split_fmt_width`/`_cfmt_for_spec` as before.
- `_gen_fstring_segment`'s numeric (`int`/`float`) branch: when a grouping
  separator is present *and* the spec body starts with a `0` followed by a
  digit (zero-pad width), captures that width via `_split_fmt_width` as
  `zwidth` before building the cfmt string (so the `0`-flag doesn't also
  reach printf, which would zero-pad to the *raw* digit count).
- New runtime helper `_runtime_group_digits_zeropad` (in: `rax`=formatted
  numeric string e.g. `"-1234567.89"`, `rbx`=target width, `rcx`=separator
  byte; out: `rax`=zero-padded+grouped string). Algorithm: compute
  `sign_len`/`intpart_len`/`frac_len` from the input string, then find the
  smallest `ndigits >= intpart_len` such that `sign_len + ndigits +
  (ndigits-1)//3 + frac_len >= width` (i.e. the *grouped* result, including
  separators, reaches `width`). Allocate `sign_len+ndigits+frac_len+1` bytes,
  write sign + zero-padding + original int digits + fraction verbatim, then
  `call _runtime_group_digits` to insert separators every 3 digits.
- New `_emit_group_digits_zeropad(sep, width)` (next to the pre-existing
  `_emit_group_digits`): sets up `rbx`/`rcx` and calls the new helper.
  `_gen_fstring_segment` calls this instead of `_emit_group_digits` when
  `zwidth is not None`.
- This path is shared by `str.format()` automatically (754a61f's
  `_gen_fstring_segment` dispatch), so `"{:015,}".format(n)` also works.
- New test `tests/cases/141_fstring_zeropad_grouping.py` (CPython-verified,
  17 print lines): positive/negative ints at various widths (incl. widths
  that don't actually require padding beyond grouping, "overshoot" cases),
  small numbers (`1`, `12`, `100`, `0`, `-1`), `_` separator, and float
  `.Nf` specs (positive/negative).

## str.format() named fields (aa44d13) — what changed

`"...".format(...)` now supports named replacement fields (`{name}`),
matching keyword arguments passed to `.format()`, freely mixable with
positional `{}`/`{0}` fields and the existing `!conv`/`:format-spec`
mini-language: `"{name} is {age}".format(name="bob", age=5)`,
`"{0} {greet}".format("hi", greet="world")`, `"{val:,}".format(val=1234567)`.

- New shared parser `ast_nodes.parse_format_fields(fmt)` (next to the
  pre-existing `parse_pct_format`): factored out of codegen's
  `_gen_str_format`, returns the same `("lit", text, "", "")` /
  `("arg", index_or_name, spec, conv)` piece list as before, except
  `index_or_name` is now a `str` (the field name) for non-digit,
  non-auto-numbered fields — previously `int(idx_part)` would raise a raw
  Python `ValueError` (crashing the compiler) for any named field.
- sema (`A.MethodCall` `"format"` branch): now also `_check_expr`s each
  `e.kwargs` value, then calls `parse_format_fields` to validate every
  field reference — a named field not in `e.kwargs` raises `str.format()
  got an unexpected field name {name!r}`; a positional index `>=
  len(e.args)` raises `str.format() field index {n} out of range (...)`; a
  field containing `.`/`[` (attribute/index access, `{0.attr}`/`{0[0]}`)
  raises a dedicated "not supported" error rather than being misparsed as a
  bogus name.
- codegen `_gen_str_format`'s `emit_piece`: for a `str`-typed `val` (named
  field), looks up the matching expr via `next(a for name, a in e.kwargs if
  name == val)` — then proceeds through the same `_gen_fstring_segment`
  dispatch as positional fields (format-spec, conversions, etc. all shared).
- `_cl_walk_expr`'s `A.MethodCall` branch now also walks `expr.kwargs`
  values (previously only `expr.args` and `expr.obj` were walked) — a
  latent gap for any non-trivial kwarg expression needing local-slot
  reservation, now exercised by `.format(name=...)`.
- New test `tests/cases/142_str_format_named.py` (CPython-verified, 9 print
  lines): named fields, mixed positional+named, named field with alignment
  spec, named field with `.2f`, repeated named field, auto-numbered `{}`
  mixed with a named field, named field with binary spec, named field with
  grouping, named field with `!r`. New
  `tests/cases_fail/str_format_unknown_field.py` for the "unexpected field
  name" error.

## Unannotated parameter/return type inference (e75cdd5) — what changed

Discovered while testing the `with` feature below: `def __init__(self, name):
self.name = name` (no annotation) made `sig.fields["name"]` default to `int`
(via `_static_value_info`'s `pinfo.get(value.name, ("int",None,None,None))`
fallback for unannotated params), so `Resource("a").name` printed a raw
pointer instead of `"a"` — reproduced even with NO `with` statement involved
(minimal repro: `class Box: def __init__(self, label): self.label = label` /
`def show(self): print(self.label)`), so this was a pre-existing,
previously-unexercised bug, not something the `with` rewrite caused. Given
the "no silent miscompiles" promise in docs.html and that idiomatic Python
rarely annotates parameters, fixed generally rather than just for `__init__`:

- New `Analyzer._literal_arg_type(value)`: `(ty,el,val,tup)` for an expression
  whose type is knowable from syntax alone (literals, f-strings, constructor
  calls to a known class), or `None` for anything scope-dependent (e.g. a bare
  `Name`). `_static_value_info` now delegates to it first.
- New `Analyzer._collect_calls(node, out)`: generic recursive
  `dataclasses.fields()`-based walker collecting every `A.Call`/`A.MethodCall`
  anywhere in a statement/expression/list subtree (module body + every
  function/method body).
- New `Analyzer._infer_unannotated_params()` (run once, before
  `_collect_field_types()`): for every top-level function and every method,
  for each parameter with no annotation and no default, gathers
  `_literal_arg_type` of the corresponding argument across all matching call
  sites (`A.Call` by function name for top-level funcs and `__init__`
  constructor calls; `A.MethodCall` by method name for other methods — method
  matching is name-only since receiver types aren't known yet at this pass,
  so a name collision across classes just falls back to `int`, same as
  before, no new miscompile). If exactly one type is seen across all
  literal-typed call sites, records it in
  `self.inferred_param_types[(qualname, param_index)]`
  (`qualname` = function name, or `"ClassName.method_name"`).
- New `Analyzer._infer_unannotated_returns()` (run right after, also before
  `_collect_field_types()`): for functions/methods with no return annotation
  and no `ret_tuple`, scans `return` statements via the existing
  `_collect_returns`; if every reachable return has a value, each value's type
  is knowable (`_literal_arg_type`, or — new — a reference to one of `fn`'s
  *own* parameters whose type is now known via annotation/default/
  `inferred_param_types`), and they all agree, sets `sig.ret_type`. This is
  what makes `def greet(msg): return msg` called as `greet("hi")` type the
  call's result as `str` (since `msg` was inferred `str` from the call site).
- `_seed_param` gained an `inferred=None` param (checked after default,
  before falling back to `"int"`); both call sites (`f.params` and
  `m.params[start:]` loops in `analyze()`) pass
  `self.inferred_param_types.get((qualname, i))`. `_collect_field_types`'s
  `pinfo` population does the same for `self.x = param` field inference.
- New test `tests/cases/143_unannotated_param_inference.py`
  (CPython-verified): `Box.__init__(self, label)` + `show()` printing
  `self.label`, `greet(msg)` returning `msg` and printing the returned value,
  `Wrapper.__init__(self, value)` + a method printing `self.value` alongside a
  parameter.

## with/__enter__/__exit__ context managers (58fd662) — what changed

`with expr as name: body` (and `with expr: body`) now works when `expr`'s
class defines both `__enter__` and `__exit__`:

- sema's `A.With` handler: when `A.expr_type(s.expr)` is `instance:ClassName`
  and the class has both `__enter__`/`__exit__` (via `_resolve_method`),
  rewrites the node **in place** (`s.__class__ = A.Try`, same pattern as the
  `@property` setter rewrite) into:
  ```python
  __cm_<id> = expr
  [name = ]__cm_<id>.__enter__()
  try:
      body
  finally:
      __cm_<id>.__exit__(None, None, None)
  ```
  then re-runs `_check_stmt` on the now-`A.Try` node — reusing the existing
  `try`/`finally` setjmp/longjmp codegen from `32762bd` with **zero codegen
  changes**. A class missing either dunder raises `SemaError("'ClassName'
  object does not support the context manager protocol (missing
  __enter__/__exit__)")`. Non-`instance:` types (e.g. `with f as x:` where `f`
  is some other static type) keep the old simple bind-and-run `A.With`
  behavior unchanged.
- New `FuncSig.returns_self: bool` + `_method_returns_self`/`_collect_returns`
  helpers (mirroring `_scan_tuple_return`/`_collect_tuple_returns`): true when
  every reachable `return` in a method with no return annotation is bare
  `return self`. The instance-method-call return-type-priority chain
  (`ret_tuple` > `ret_type` > `returns_self` > `"int"`) gained the
  `returns_self` branch: `e.inferred_type = obj_t` (the receiver's own
  `instance:ClassName` type). This is what makes `r = Resource("a") as r:` (the
  synthetic assign from `__enter__()`) type `r` as `instance:Resource` instead
  of `int`, so `r.use()` resolves. General-purpose beyond `__enter__` — any
  self-returning builder-style method benefits.
- New test `tests/cases/144_with_context_manager.py` (CPython-verified):
  `with Resource("a") as r:`, `with` on a pre-existing instance
  (`with r2 as r3:`), and exception propagation through `with` (a
  `maybe_raise(n)` helper that raises inside the `with` body — `__exit__`
  still runs before the exception propagates to an outer `try`/`except`). New
  `tests/cases_fail/with_missing_dunders.py` for the missing-dunders error.
- docs.html: new "Context managers (`with`)" subsection under Classes;
  removed `with`/context managers from the limitations table (added "multiple
  context managers in one `with`" as the remaining gap); updated the Classes
  limitations row to list `__enter__`/`__exit__` alongside `__init__`/`__str__`.

## Remaining format-spec gaps (not yet implemented)

Candidates for a future iteration:
- `str.format()` attribute/index access in fields (`"{0.attr}"`,
  `"{0[0]}"`) — now raises a clear "not supported" semantic error (aa44d13)
  rather than being silently misparsed, but is not implemented. Lower
  priority: rare in practice, and would require either a mini sema pass on
  a synthetic Attr/Subscript node at codegen time or pre-resolving the
  access during the field-parsing pass.

## Dict insertion-order iteration (86977c0) — what changed

Fixed the long-known bug (see old "Known issue noticed in passing" section,
now removed): dicts now iterate in CPython 3.7+ insertion order everywhere,
instead of the open-addressed hashtable's bucket order.

- **New ABI field**: the dict/set/instance header gains `order_buf`
  (`DICT_ORDER_OFF = 32`), an array of `cap` key pointers; the first `len`
  entries are the live keys in insertion order. Header size `DICT_HEADER`
  32 -> 40 bytes. New helper `_emit_dict_alloc_order_buf(cap, slot_off)`
  allocates+zeroes it; called at all ~10 header-creation sites (dict/set
  literals & comprehensions, `.copy()`, set-op results, class instances,
  `field(default_factory=dict)`).
- `_runtime_dict_set`: on the `._ds_new` (newly-inserted key) path, appends
  the key pointer to `order_buf[len]` before incrementing `len`. In-place
  value updates don't touch `order_buf` (position is preserved).
- `_runtime_dict_grow`: allocates a new (larger) `order_buf` and `memcpy`s
  `order_buf[0..old_len)` verbatim — key pointer identity/order is
  grow-invariant, only the buffer is resized.
- `_runtime_dict_pop`: finds the popped key's slot in `order_buf` by pointer
  equality and shifts subsequent entries left by one to close the gap.
- `_runtime_dict_keys`/`_runtime_dict_values`/`_runtime_dict_items`,
  `_runtime_dict_repr`, `_runtime_dict_update`, and `_gen_for_dict` (`for k
  in dict:` / `for x in set:`) all rewritten to walk `order_buf[0..len)`
  directly — for values/items/repr, each key's value is fetched via
  `_runtime_dict_lookup_slot(dict, key)` (key saved to a stack local first,
  since that helper clobbers r8-r11). This collapsed the old separate
  slot-index/write-index pair into a single index for `.items()`.
- Side effect: `_runtime_dict_update` (used by `|`/`|=`/`{**a,**b}`) now
  merges new keys from `src` in *src's* insertion order — matches CPython's
  actual dict-merge semantics exactly (previously it walked `src`'s bucket
  order).
- Updated `tests/cases/56_dict_keys_values.py` (was deliberately encoding the
  *old* bucket-order output with a comment explaining why; now expects
  CPython's real insertion order `alice, bob, carol` and removed that
  comment). New `tests/cases/134_dict_order.py` (CPython-verified):
  print(dict), `for k in d`, `.items()`, dict comprehension, key append via
  `d[new]=...`, `del`/`.pop()` gap-closing with order preserved, and
  PEP 448 unpack ordering.
- Set iteration order: sets reuse the dict layout, so they now also iterate
  in *their* insertion order via the same `order_buf`/`_gen_for_dict` path
  (previously bucket order). CPython doesn't guarantee set order either way
  (it's hash-based and differs from both), so this is a behavior change but
  not a parity regression — `134_dict_order.py` doesn't print a whole set
  for this reason (verified manually during dev that asmpython's set order
  differs from CPython's, as expected).

## Dict literal unpacking (8066154) — what changed

- parser (`_parse_brace`): a leading `**` immediately after `{` is
  unambiguously a dict literal (set literals can't contain `**expr`), so it's
  parsed via a new loop accepting either `**expr` (spread) or `key: value`
  pairs in any order/count, e.g. `{**d1, "k": v, **d2}`. The pre-existing
  `key: value`-first path (dict literal / dict comprehension) also now accepts
  `**other` after a comma alongside further `key: value` pairs.
- AST: `A.DictLit.keys: list[Expr | None]` — a `None` entry pairs with the
  spread expression at the same index in `values`. Updated docstring.
- sema (`_check_expr` A.DictLit): two `zip(keys, values)` passes now skip/handle
  `k is None` entries — validates each spread's value is `dict`/`any`
  (`SemaError("dict unpacking requires a dict (got {vt})")` otherwise), and
  folds the spread's value-kind (via `_dict_value_type`) into the homogeneous
  value-type inference alongside explicit-key values (an `"any"`-typed spread
  is compatible with any value kind, same as other "any" values).
- codegen: `_cl_walk_expr` walks spread expressions too (for local-slot
  collection). `_gen_dict_lit`'s per-pair loop: for `k_expr is None`, evaluates
  the spread to a dict header in `rax` and calls `_runtime_dict_update(dst=the
  literal's dict, src=that header)` — same helper as PEP 584's `|`/`|=`. Pairs
  are processed in source order, so later entries (spread or explicit) win on
  conflicts, matching CPython.
- New test `tests/cases/133_dict_unpack.py` (CPython-verified): two-way merge,
  override via explicit key after a spread, spread-then-spread-then-override,
  non-mutation of source dicts, and spread-only shallow copy. New
  `tests/cases_fail/dict_unpack_bad_type.py` for the
  `dict unpacking requires a dict (got int)` error.

## Dict union operators (8e1ae65) — what changed

- sema: `d1 | d2` -> `dict`, with the result's value type inferred from
  whichever operand has a known (non-default) `dict_value_types` entry
  (`_dict_value_type` gained an `A.BinOp` case for `op == "|"`). `d |= other`
  requires `other` to be `dict`/`any`, else `SemaError("unsupported operand
  type for |=: dict |= <T>")`.
- codegen: both `d1 | d2` and `d1 |= d2` lower to the existing
  `_runtime_dict_update(dst, src)` helper. `d1 | d2` reuses
  `_gen_set_setop(..., "union", ...)` (build a fresh dict, `update(left)`,
  `update(right)` — right wins on key conflicts), the same path already used
  for `set | set`. `d1 |= d2` is a new AugAssign branch: `_runtime_dict_update`
  directly on the target's existing header (pointer doesn't change).
- **Found and fixed a real bug in `ast_nodes.expr_type`**: the `BinOp` case
  only special-cased `inferred_type in ("type", "any", "set")` before falling
  through to "bitwise op on `|`/`&`/etc -> always int". Sema's new `"dict"`
  stamp for `d1 | d2` was being ignored, so `d3 = d1 | d2; d3["a"]` failed
  with "cannot index a int". Added `"dict"` to that tuple
  (asmpython/_compiler/ast_nodes.py ~line 860).
- New test `tests/cases/132_dict_union.py` (CPython-verified): basic union,
  `|=` in-place merge (and that the *other* operand is left unchanged), 3-way
  chained union (`{"x":1} | {"x":2,"y":3} | {"y":4,"z":5}`), and str-valued
  dicts. New `tests/cases_fail/dict_union_bad_type.py` for the `|=`
  type-mismatch error. All key access is via `d[k]`/`len(d)`, never printing
  a whole dict directly, to avoid the still-open dict-iteration-order bug
  (below).

## try/except dispatch fix (32762bd) — what changed

Previously, a `try` with more than one `except` clause always ran the
*first* handler's body regardless of the raised exception's actual type —
e.g.
```python
try:
    raise KeyError("k")
except TypeError:
    print("type")
except KeyError as e:
    print("key", e)
```
printed `type` (wrong); CPython prints `key 'k'`. This is now fixed:

- Every exception now carries a runtime type id in a new `.bss` global
  `_runtime_exc_type` (parallel to the existing `_runtime_exc_msg`).
  `_runtime_raise` now takes `rax` = message, `rbx` = type id.
- `EXC_ANY = 0` is a wildcard id for "untyped" raises (`raise "a string"`,
  or raising a `str` variable) — it matches *any* `except` clause (typed or
  not), preserving the old "catches anything" behaviour for code that raises
  bare strings (tests 25/26/27/28/66/95 all rely on this and still pass).
- `BUILTIN_EXC_PARENTS` / `BUILTIN_EXC_IDS` (codegen.py, near top of file)
  give every builtin exception a fixed id 1..21 and record CPython's parent
  hierarchy (e.g. `KeyError`/`IndexError` -> `LookupError` -> `Exception` ->
  `BaseException`; `ZeroDivisionError`/`OverflowError` -> `ArithmeticError`).
  `IOError` aliases `OSError`'s id (same as CPython, `IOError is OSError`).
  User exception classes (deriving from a builtin exception) get ids
  N+1.. assigned in declaration order via `Codegen.__init__` ->
  `_exc_type_id`.
- New `Codegen` helpers: `_cg_is_exception_class`, `_exc_type_id`,
  `_exc_is_a`, `_exc_matching_ids`, `_exc_raise_type_id`.
- AST: `A.Try` gained `handler_types: list[str]` (first handler's declared
  type(s)); `extra_handlers` is now `list[(types: list[str], bind_name,
  body)]` (was `(bind_name, body)`). Parser (`_parse_try`) captures
  `except E:` / `except (E1, E2):` as `list[str]` (bare `A.Name` or
  `A.TupleLit` of `A.Name`s; raises `ParseError` for anything else). Sema
  validates every captured type name via new `_check_exc_type_name`
  (`BUILTIN_EXCEPTIONS` or `_is_exception_class`).
- `_gen_try` fully rewritten: after longjmp, walks handlers in source order;
  for each typed handler, compares `_runtime_exc_type` against
  `_exc_matching_ids(types)` (the declared types + their known subtypes +
  `EXC_ANY`) via a chain of `cmp`/`je`; bare `except:` always matches. If no
  handler matches, `finally` runs and `_runtime_raise` re-raises (the
  exception propagates). `_cl_walk`'s `A.Try` branch now also walks
  `extra_handlers`/`else_body`/`finally_body` bodies and reserves bind-name
  locals for every handler (previously only the first handler's locals were
  collected — a latent bug for any handler-local variables in
  extra_handlers, now fixed as a side effect).
- Also fixed two other `extra_handlers` 2-tuple unpacks that broke when the
  shape became 3-tuples: `codegen.py::_collect_frame_bound` and
  `program.py`'s import-walker.
- New test `tests/cases/131_except_dispatch.py` (CPython-verified): multi-
  clause dispatch by type, `except LookupError:` catching `IndexError`,
  `except (TypeError, ValueError):` tuple form, finally-then-propagate to an
  outer handler, try/except/else, and an internal `ValueError` from
  `list.index()` falling through a non-matching `except KeyError:` to a
  matching `except ValueError:`.
- New `tests/cases_fail/except_unknown_type.py`: `except NotAnException:`
  (undefined name) is now a sema error `'NotAnException' is not an exception
  type`.
- `--use-runtime-lib` mode is pre-existingly broken (20/172 before this
  session, 21/172 after — not a regression, not investigated; the default
  `py -m tests.runner` mode is what the standing workflow checks).

## Scope note: enumerate(zip(...), start) is NOT supported

`_for_zip_spec` (sema.py ~1265-1290) recognizes the combined
`for i, (a, b) in enumerate(zip(A, B))` shape but hardcodes
`len(it.args) == 1` for the enumerate call, so `enumerate(zip(A, B), 1)`
falls through to the plain-enumerate path instead. Judged out of scope as a
rare pattern; only plain `enumerate(xs, start)` was implemented. Revisit
only if a real test case needs it.

## Next steps for the parity loop

1. Check `.claude/issues` for new repro cases (has been empty all session).
2. Pick next gap. Candidates not yet investigated:
   - `match`/`case` (structural pattern matching) — large.
   - Multiple context managers in one `with` (`with a, b:`) — small follow-up
     to 58fd662; would desugar to nested `with` statements at parse/sema time.
   - `nonlocal` — asmpython doesn't model nested functions/closures, may be
     out of scope ("closures aren't modelled" per existing comments).
   - Additional `*`/`**` unpacking forms beyond what's documented
     (`f(*t)` for statically-known tuples is supported; `f(*g())` is not).
   - `async`/`await` — likely explicitly out of scope for a systems compiler.
   - User-defined exception classes in `raise`/`except`: investigated this
     session but NOT implemented — `_exc_type_id` gives RTTI ids to user
     classes deriving from `Exception`, but `raise MyError("x")` evaluates
     the constructor call via `_gen_constructor`, which returns an *instance*
     pointer (a dict-like object), not a string. `_runtime_exc_msg` /
     `except ... as e:` are hardcoded to treat the payload as a `str`
     (sema.py ~2170: `scope.add(s.bind_name, "str")`). Supporting
     `class MyError(Exception): ...` + `raise MyError("custom msg")` +
     `except MyError as e: print(e)` properly would mean either (a) extracting
     a message string from the instance at `raise` time (e.g. from a known
     `.args`/`.message`-like field) and storing that in `_runtime_exc_msg`
     while keeping the instance pointer separately for attribute access on
     `e`, or (b) a bigger change making `_runtime_exc_msg` hold an instance
     pointer generally and teaching `print()`/`str()` on a caught exception
     to call `__str__`. Either is a real architecture change to the
     "string-message based" exception runtime (see sema.py ~195) — scope
     carefully, likely multi-session. Plain `raise ValueError("msg")` /
     `except ValueError as e: print(e)` (builtin types, no custom `__init__`)
     already works fine since `ValueError("msg")` isn't a user class so
     `_exc_raise_type_id` still tags it correctly, but `gen_expr` on
     `ValueError("msg")` — check whether builtin exception "calls" are
     special-cased to evaluate to the message string rather than going
     through `_gen_constructor` (likely yes, since `tests/cases/131_*` and
     many earlier tests already `raise ValueError("...")` / `raise
     TypeError("...")` and pass). The gap is specifically *user-defined*
     exception subclasses.
3. Workflow per feature (standing directive): implement in
   sema.py/codegen.py (+ target_*.py if needed) -> add a CPython-verified
   test in `tests/cases/*.py` with `# expect:` (or `tests/cases_fail/*.py`
   with `# expect-error:`) -> `py -m tests.runner` green -> update
   `docs.html` -> update `CHANGELOG.md` under `[Unreleased]` -> commit.

## Standing directives (always apply, from memory)

- "extend asmpython to support, don't edit to make compatible"
- "don't make minimal versions: go full for everything"
- never write `-> "ClassName"` quoted forward-ref annotations
- commit regularly at checkpoints (overrides default "only commit when
  asked")
- regularly check `.claude/issues` for new failing repro cases (always
  empty so far)
