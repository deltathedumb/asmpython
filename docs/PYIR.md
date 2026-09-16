# PYIR — the level above UIR

**Status: a design, not an implementation.** Nothing in `src/` implements this
yet. It is written down because the measurements below are the argument for
building it, and because the first two steps are worth doing whether or not
the rest ever happens.

## The measurement

`asmpython build` compiles Python to a native binary with no interpreter in
it. On a compute loop that binary is **19x slower than CPython**:

| | fib(27) + a 3,000,000-iteration loop |
|---|---|
| CPython 3.14 | 0.156s |
| asmpython, dynamic path, `-O` | 2.97s |
| asmpython, statically annotated (`n: i64`) | 0.008s |

The third row is the same compiler and the same backends, so the codegen is
not the problem. The problem is what the dynamic frontend hands the backends.
Here is `while i < n: t = t + i * i; i = i + 1` after `-O`, abbreviated:

```
%9  = ptr.call @apy_lt(%2, %1)     →  apy_error_occurred → branch
%14 = i64.call @apy_truth(%8)      →  the loop test
%17 = ptr.call @apy_mul(%2, %2)    →  apy_error_occurred → branch
%22 = ptr.call @apy_add(%3, %17)   →  apy_error_occurred → branch
%28 = ptr.call @apy_from_int(%27)  →  the literal 1, rebuilt every iteration
%29 = ptr.call @apy_add(%2, %28)   →  apy_error_occurred → branch
```

**Five calls and four error checks per iteration to perform four machine
instructions.** That is the whole gap, and none of it is reachable by a
backend: by the time anything sees this, the arithmetic is an opaque `CALL`.

## Why a level and not a pass

The Python frontend names **262 distinct `apy_*` symbols across 506 call
sites**. That set *is* a high-level instruction set — "multiply two Python
objects", "walk this iterable", "look up an attribute" — but it is encoded as
`CALL` with a string symbol. The verifier checks the arity and that the
operands are `ptr`; it cannot know that `apy_getattr` may raise, that
`apy_mul` on two int cells is pure, or that `apy_from_int` always yields an
int. So no pass can fold, specialise or reorder any of it.

asmpython therefore already has two IRs. The upper one is undocumented and
expressed in a form nothing can reason about. PYIR is that level, written
down.

## The decision that shapes everything: guards, not deoptimisation

CPython, PyPy and V8 speculate and **deoptimise** — a failed guard bails to an
interpreter. asmpython is ahead-of-time and ships no interpreter, so it
cannot. That sounds like a limitation and is not: it means specialisation is a
**guard and a branch**, with both paths compiled in and merging back to a
`ptr`.

```
if both operands are int cells:   i64.mul, overflow check, box
else:                             call apy_mul
```

No deopt machinery, no OSR, no side tables — a branch that is cheaper than the
call it replaces. Where analysis *proves* the types, the guard is dropped;
where it only suspects them, the guard stays. Both produce the same shape of
lowered UIR, which is what keeps the design small.

## What belongs at this level

The criterion is sharp: **an operation belongs in PYIR if and only if knowing
its operand types changes its lowering.** Everything else stays a `CALL` into
the runtime. PYIR does not replace those 262 symbols; it exposes the part
worth optimising, which is about 35 opcodes.

| group | ops | why it is here |
|---|---|---|
| arithmetic | `add sub mul floordiv truediv mod pow neg` | int/int and float/float have machine forms |
| comparison | `lt le gt ge eq ne` | same, and `eq` on interned strings is a pointer compare |
| bitwise | `and or xor shl shr invert` | integer-only anyway |
| truth | `truth` | every `if` and `while`; free on a known bool or int |
| representation | `box <ty>` / `unbox <ty>` | the load-bearing pair — `box(unbox(x))` is `x`, and a run of arithmetic between a box and an unbox collapses wholesale |
| constants | `const_int` `const_str` … | a boxed literal becomes a VALUE, hoistable, rather than a call |
| sequence | `getitem setitem len` | a list and an int index is a bounds check and a load |
| iteration | `iter next` | a `for` over a `range` or a `list` becomes a counted loop |
| objects | `getattr setattr call` | with a known class: a slot offset, and later, inlining |
| effects | `may_raise`, an explicit exception edge | see below |

### The effects row

Every fallible call is followed today by a hand-rolled `apy_error_occurred`
test, because nothing knows which operations can fail. Modelling raising as a
PROPERTY of the operation means the lowering emits that test once, and a pass
can delete it wherever the operation provably cannot raise.

**Not a free win, and an earlier note in this project said otherwise.** The
four checks in the loop above are after `apy_lt`, `apy_mul` and two
`apy_add`s, every one of which genuinely can fail for general operands —
`"a" + 1` raises. Deleting them needs the type knowledge, not just the effect
model. What the effect model buys is that once a guard has established two int
operands, the fast path's arithmetic carries no check at all, and the check
that remains is the one on the slow path where it belongs.

## The type lattice

Not Python types — REPRESENTATION types:

```
⊥ | i64 | f64 | bool | str | bytes | list | tuple | dict | <class C> | ⊤
```

plus one fact that does the real work: *not an instance of anything overriding
this operator.*

Two open divergences bear directly on that fact, and both must be settled
before any arithmetic specialisation is sound:

* **A class cannot currently extend `int`** (a compile error). That makes
  `int` final, which makes `a + b` on two int cells unable to dispatch to a
  user `__add__` — so the fast path needs no class check. Fixing the
  divergence removes that guarantee, and the guard must then test *exactly*
  int rather than int-or-subclass. It has to be written that way from the
  start or the fix silently miscompiles.
* **An inherited builtin operator loses to a written mirror dunder.** That is
  a question about exactly when a class's own method beats the builtin's, and
  it is the same question the specialiser has to answer.

## Where it sits

```
AST  →  PYIR  →  UIR  →  backends
```

PYIR is **legitimately Python-specific**, and that asymmetry is the whole
argument for a separate level rather than dialects inside UIR: UIR stays
machine-level and language-neutral, PYIR knows what a Python object is. So it
carries Python's name where UIR must not.

PYIR always lowers before anything else sees it. `ir/interpreter.py` never
learns it, so `asmpython run` stays the oracle the corpus is diffed against.

## What this is not

**Not pluggable IRs, and not dialects.** `opcodes.py` says a backend author
reads that file and nothing else; the moment a plugin can add opcodes, every
backend must cope with opcodes it has never seen. MLIR needed on the order of
200k lines and a decade of institutional backing to make that work. A second
frontend needing operations UIR cannot express is the only thing that should
reopen it, and UIR is deliberately machine-level — the level everyone
converges on.

Note that `plugins/patch.py` already makes it *possible*: a plugin can wrap or
replace anything, with the verifier and the four registries guarded behind
`force=True`. The question was never whether someone can bolt an IR on; it is
whether this project supports it, and supporting an extension point means
freezing what sits behind it — at the exact moment UIR is known to need SSA.

## Order of work

Each step is measurable against the loop above, which is the discipline that
keeps this honest.

1. **Build each immutable literal once per function.** No new IR, no analysis.
   It closes a conformance divergence as well as a cost: two occurrences of
   one literal in a function are currently two objects, where CPython shares
   one code-object constant, so `a = 1000; b = 1000; a is b` is False here and
   True in CPython. `_dyn_str_literal` already claims otherwise in its own
   docstring — it interns the BYTES and then builds a fresh cell at every use
   site.
2. **`box` / `unbox`, and the peephole that cancels adjacent pairs.** This is
   where PYIR starts existing.
3. **The lattice and within-function inference.** Local first; whole-program
   much later, if ever.
4. **Guarded arithmetic.** The first specialisation, and the first number.
5. **`getitem` / `iter`, then `getattr` / `call`.**

Steps 1 and 2 need no level at all and are worth doing on their own terms.
They are also the cheapest evidence of whether the rest pays.
