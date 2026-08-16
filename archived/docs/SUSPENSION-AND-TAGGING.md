# Suspension and tagging

Two mechanisms that most of the compiler quietly depends on, and that are easy
to break from a distance: how a value survives losing its static type, and how
a function survives being stopped in the middle.

They are documented together because they meet: a coroutine's result crosses
both boundaries at once.

---

## Part 1 — the tagged-value ("any") model

### The problem

asmpython is statically typed, but Python is not. A value whose static type is
`any` — an `object` parameter, an element of a heterogeneous list, a value read
out of `dict[str, object]`, an unannotated `*args` element — reaches a slot
that says nothing about what it is. `type()`, `isinstance()`, `str()` and
arithmetic all need to know.

### The invariant

> **An `any` slot always holds a BOXED value, and every read of an `any` slot
> unboxes exactly once.**

Both halves are required. Either alone is worse than neither:

- store-side only → readers see a box where they expect a value;
- read-side only → readers unbox something that was never boxed.

### The two choke points

| | |
|---|---|
| **Store** | `_lower_value_into_any_slot` (via `_lower_for_slot`) |
| **Read** | `_lower_expr` |

Every assignment, field set, container element, parameter pass and return goes
through the store choke. `_lower_expr` is the read choke: if the expression's
static type is `any` and its IR type is `PTR`, it unboxes.

That `PTR` condition matters more than it looks. **A value typed `I64` slips
straight past the read choke**, so any site that loads an `any` value must type
it `PTR` or the box pointer flows onward as if it were the value. Several bugs
have come from exactly this — a list-element load, a dict-element load, and an
`any`-object dict read each defaulted to `I64`.

### The cell

    [ BOX_MAGIC ][ tag ][ payload ]        24 bytes, from _abi_new_box

`tag` is a `BUILTIN_TYPE_IDS` entry. `BOX_MAGIC` at offset 0 is what lets a
reader identify a box with one fault-safe load.

Boxed kinds: `int`, `float`, `bool`, `str`, **and** `list`, `dict`, `tuple`,
`set`. Containers were once passed through unboxed on the theory that a
container "already carries its own runtime shape". It does not — a list, a
dict and an instance are all just pointers — so `isinstance(o, dict)` answered
False for an actual dict. A float's payload is its *bit pattern*
(`bitcast_f2i`), so reading one back needs a reinterpret, not a convert.

Not boxed: a user instance (it already carries a real `__class__` tag), a
callable (boxing a function pointer would make a later call invoke the cell),
and `None` (a plain 0 everywhere, so every existing null check keeps working).

### What is still a guess

`_lower_read_any_tag` decides "is this word a pointer?" with
`> 0x10000 && 8-byte aligned`, then probes for `BOX_MAGIC`. That guess exists
only because **there is no object header**. It is the root cause of more than
one fixed bug, and removing it is the first step of the GC work — see
ROADMAP's memory-management entry.

### Consequences worth knowing

- **Identity survives boxing.** Each box is a fresh cell, so `a is b` could
  have broken for two references to the same list. It does not, because the
  read choke unboxes *both* operands before comparing.
- **`x is True` needs the tag.** In CPython `1 is True` is False. asmpython has
  no separate bool type, so once unboxed the two are the same integer — the box
  is the only thing that still distinguishes them (tag −4 vs −1), so the
  comparison reads the tag before unboxing.
- **UNTAGGED counts as `int`.** A never-boxed word in an `any` slot reports
  UNTAGGED; it is treated as an integer, which is the same assumption the
  formatter's fallback makes.

### Formatting

`_runtime_fmt_elem` takes an element *kind*: low nibble is the base kind, high
nibble the kind one level down. That encodes a statically known shape, which a
heterogeneous container does not have — so **kind 6 means "this element is
boxed, dispatch on its tag"**, and it recurses with itself. That recursion is
what makes nesting work to any depth without the compiler knowing the shape.

---

## Part 2 — suspension: generators and coroutines

### One mechanism, two drivers

A generator and a coroutine are the same machine. Only the driver differs:

| | resumed by | yields |
|---|---|---|
| generator | `for` / `next()` | a value |
| coroutine | an event loop, via `send(v)` | the awaitable it is blocked on |

### The encoding

The body is flattened into basic blocks and emitted as a dispatch:

```python
def __next__(self):          # or send(self, value)
    while True:
        if self._state == 0:
            <block 0>
            <terminator>
        if self._state == 1:
            ...
        raise StopIteration()
```

Terminators lower to:

| terminator | emits |
|---|---|
| `goto T` | `self._state = T; continue` |
| `branch` | `if test: state = A; continue` / else B |
| `yield v` | `self._state = T; return v` |
| `await x` | `self._awaiting = x; self._state = T; return self._awaiting` |
| `stop` | `raise StopIteration()` (generator) / flag `_done` and return (coroutine) |

No `goto` is needed: the enclosing `while True:` plus `continue` **is** the
dispatch. A resume point is a state number, so there is no limit on how many
there are or where they sit.

Locals are lifted to fields so they survive a suspension. A statement
containing no suspension and no loop-level `break`/`continue` is copied into
its block **whole**, so ordinary control flow inside a generator lowers exactly
as it would in a normal function.

### Things that bite

- **A `for` loop's target is a plain string** on `A.For` and cannot be renamed
  to `self.x`. It binds a fresh name and copies into the field on entry.
- **Narrowing an opaque value tells you the container kind, not the element
  kind.** `if isinstance(o, list): for x in o:` must keep `x` opaque; typing it
  `int` makes an already-boxed element get boxed *again*, and one unbox then
  yields the inner box pointer.
- **A coroutine's return value must not go through a field.** A field's type is
  fixed by what `__init__` seeds it with, so a `str` result in an int-seeded
  field is stored raw and reads back as a pointer. It is returned through
  `send` instead, whose return slot is declared `any` — so it passes through
  the boxing choke and keeps its tag.

### `await` is an expression, suspension is a statement

The flattener splits at statements, so `await_normalise.py` rewrites each
statement into A-normal form first:

```python
total = total + await fetch(u)
# becomes
_aw1  = fetch(u)
_aw1r = <suspend on _aw1>
total = total + _aw1r
```

Awaits hoist left-to-right so side effects keep their order, and
`await f(await g())` lifts the inner one first. An await under `and`/`or`, in a
conditional expression, a comprehension or a lambda is **declined** rather than
rewritten — hoisting a conditionally-evaluated operand would make it
unconditional, which silently changes meaning.

### `asyncio.run` is a trampoline

An awaited coroutine is *pushed onto a stack* and stepped in its parent's
place, not entered recursively. Nesting therefore costs stack entries rather
than native frames, and a coroutine awaiting in a loop does not grow the
machine stack at all.
