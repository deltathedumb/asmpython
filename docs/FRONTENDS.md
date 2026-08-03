# Writing a frontend

A frontend turns source text into a `Module`. It is the mirror image of a
backend and about the same size.

```python
from asmpython.frontend import Frontend, register
from asmpython.ir import Module

class MyLang(Frontend):
    name = "mylang"
    extensions = (".ml",)
    description = "the My-Language surface syntax"

    def compile(self, source, sink) -> Module | None:
        ...

register(MyLang())
```

Then `asmpython build prog.ml`, or `--frontend mylang` if the extension is
ambiguous.

## Everything language-specific lives on your side

The IR has no opinion about Python's `//` flooring toward negative infinity,
about boxing, or about dynamic dispatch. Those are things a frontend *lowers*
into the IR's small vocabulary plus whatever runtime functions it decides to
call.

`Op.DIV` truncates toward zero, like C and like every machine. If your
language floors, you emit a division and a correction — several instructions,
paid once here, rather than owed by each backend. That is the whole argument
for a small IR.

There are also **no I/O opcodes**. Printing is a call to a named function you
declare external; you choose the runtime that defines it, and the link stage
supplies one for the Python frontend (see [LINKERS.md](LINKERS.md)).

## Errors are reported, never raised

```python
def compile(self, source, sink):
    ...
    if problem:
        sink.report(error("E1001", "what is wrong")
                    .at(span, "this bit")
                    .note("why it matters")
                    .help("what to write instead"))
        return None
```

Returning `None` means "I reported to the sink". A frontend never raises for
bad user input.

**A construct your analysis accepts, your lowering must handle.** That
contract is where the sharpest bug in this tree lived: `**` type-checked as
ordinary arithmetic and then hit a lowering table of five operators, so the
user got a `KeyError` with a compiler stack in it. `@` did the same. Check the
constructs you lower against an explicit list in the analyser, where a user
can see the refusal, and make your lowering's fallthrough `raise` rather than
silently emit nothing:

```python
case _:
    raise AssertionError(
        f"lowering reached {type(node).__name__}; analysis accepted "
        f"something lowering does not handle")
```

The test that finds this class of bug is cheap: feed a corpus of unsupported
source at the whole pipeline and assert only that a compiler behaves like one
— a result or a diagnostic, never an exception. See
`tests/asmpython/unit/test_frontend.py::TestNothingCrashes`.

## Registers are mutable, and there are no phi nodes

Where SSA would need a phi, assign the same register on both paths:

```python
out = b.reg(T.I64)
b.branch(cond, then_b, else_b)
b.switch_to(then_b); b.copy(out, x); b.jump(join)
b.switch_to(else_b); b.copy(out, y); b.jump(join)
b.switch_to(join)                     # `out` holds whichever ran
```

Allocate a local's register **up front**, before lowering the body, so an
assignment inside a branch writes the register the join reads. Allocating
lazily is exactly what would need phis.

## Spans

Give every instruction the source range it came from, using the same
computation your diagnostics use:

```python
self.b.span = span_of(self.source, node)
```

One function for both, or they drift — and then a backend's error message
lands a column off the type error reported for the same expression. In this
tree the lowerer's span helper ended in `if False else self.b.span`, so every
instruction silently claimed to come from whatever statement was lowered last.

## Testing it

Your language's own semantics are the oracle. For Python that is CPython
itself, run on the same source:

```python
want = cpython_output(src)
got = interpret(compile_module(src))
assert got == want
```

Run it against the **optimised** IR too. A pass can be individually correct
and still change meaning in combination, and nothing else finds that.

## Checklist

- [ ] `name`, `extensions` and `description` set; `register()` called
- [ ] every user error is a diagnostic with a code, a span, and a suggestion
- [ ] every construct analysis accepts, lowering handles — and lowering's
      fallthrough raises rather than emitting nothing
- [ ] locals get their registers before the body is lowered
- [ ] instruction spans come from the same helper the diagnostics use
- [ ] the module passes `verify()` — the driver checks, and blames you by name
- [ ] tested against your language's reference implementation, on optimised
      and unoptimised IR

## Getting it loaded

`register()` runs when your module is imported, and nothing imports it for
you. Declare what you provide and install it once:

```python
from asmpython.plugins import Plugin

plugin = Plugin("mypack")
plugin.backends.append(MyBackend)         # a class or an instance, either
__asmpython_plugin__ = plugin
```

```bash
asmpython plugin add mypack        # remembered; loaded on every run afterwards
asmpython plugin show mypack       # what it provides, registering none of it
asmpython plugin list | remove
```

`add` looks in the working directory, then the Python path, then pip --
`--cwd 1|0`, `--pypath 1|0`, `--pip 1|0`, with pip off unless asked.

### Replacing and refreshing

`plugin add` on a name that is already installed ASKS before replacing it,
because replacing clears a cached copy that may be the only one left -- the
origin it came from is not guaranteed to still exist. `--yes` and `--no`
answer without asking, and a non-interactive run declines rather than
prompting: a build that blocks forever on a hidden question is worse than one
that stops and names the flag.

An installed plugin is CACHED and loaded from the cache, not from wherever it
was found, so an install keeps working when the original file moves or the
compiler is run from another directory. `origin` stays recorded for exactly
one purpose:

```bash
asmpython plugin invalidate mypack           # one id
asmpython plugin invalidate a,b              # comma-separated
asmpython plugin invalidate a b              # or repeated
asmpython plugin invalidate --all
```

`invalidate` goes back to the origin, re-resolves, and refreshes the cache --
which is how an edited plugin under development is picked up. If the origin is
gone it fails and says so, leaving the cached copy in place: a cache that no
longer matches any real source is exactly the state worth being told about.

Without installing: `--plugin mypack` for one invocation, `ASMPYTHON_PLUGINS`
for a CI job, or an `asmpython.plugins` entry point if you ship a
distribution. Declaring a manifest is better than calling `register()` at
import time, which also works: a manifest can be READ, so `plugin show` and
the install-time report can say what a module provides without letting it
change the compiler's state first.
