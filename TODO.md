# asmpython refactor todo list

| Task                                      | Status    |
|-------------------------------------------|-----------|
| Finish closing asmpythons conformance gap | In-flight |

The goal: `python conformance/harness.py --shim asmpython` passes every counted
case. CPython passes 100% of them, so any divergence is asmpython's bug.

## Where it stands

|  | score |
| --- | --- |
| the PRE-REWRITE compiler, measured by accident through site-packages, on a 585-case suite | 712/1634 |
| `results/rewrite_zero_baseline.json` — the first run against `src/` | **0**/1668 |
| after the dynamic object model, containers, exceptions, globals, defaults | 888/1668 |
| with classes, closures and big integers | 1043/1668 |
| with bytes | 1094/1668 |
| with `del`, the walrus, `f(*xs)` and the `finally` fixes | 1105/1668 |
| with big-integer literals and exception payloads | 1143/1668 |
| with complex — every value kind Python has | 1165/1668 |
| with builtins as values, and the small builtins | 1225/1668 |
| with lambdas, keyed sorting, unbound methods | 1239/1668 |
| with iterators, `*` displays, augmented subscripts | 1273/1668 |
| with `with`, keyword arguments, `**kwargs`, decorators, starred targets and the multi-shape builtins | 1304/1668 |
| with exception chaining and the mutating container methods | 1320/1668 |
| with the iterator protocol, and exceptions that leave a function instead of the process | 1325/1668 |
| with the format mini-language, in all three spellings that share it | 1333/1668 |
| with `__dict__`, dict union and the function dunders | 1338/1668 |
| with the in-place operators, and `finally` on every exit | 1356/1668 |
| with rich comparisons, the unary dunders and `__index__` | 1361/1668 |
| with `__getattr__`, private name mangling and the remaining str/bytes methods | 1364/1668 |
| with `import`, and `math` behind it | 1369/1668 |
| with `...` as a singleton | 1370/1668 |
| with comprehension scope, `print(sep=, end=)` and `enumerate(start=)` | 1374/1668 |
| with generators: `yield`, `send`, `close` | 1377/1668 |
| with `yield from` and `throw` | 1380/1668 |
| with iteration that ADVANCES rather than walking by index, and backend-supplied modules | 1382/1668 |
| with lazy cursors, `/` and `*` parameters, general `isinstance`, and `try` joins that respect a handler that cannot fall through | 1385/1668 |
| with runtime `UnboundLocalError`, the `object` defaults, and `__getattribute__`/`__setattr__` interception | 1389/1668 |
| with `__slots__`, generator expressions that are real generators, and `any`/`all` short-circuiting | 1406/1668 |
| with `typing`'s inert half, function attributes, three-argument `getattr`, and `hash` asking the class | 1415/1668 |
| with the format mini-language's presentation types — and `apy_as_float` no longer reading an int's bits as a double | 1418/1668 |
| with coroutines: `async def`, `await`, `asyncio.run`, `gather` | 1423/1668 |
| with async generators, `async for` and async comprehensions | 1427/1668 |
| with set iteration in CPython's order, and `__eq__` without `__hash__` refusing | 1436/1668 |
| with the descriptor protocol — `property`, `classmethod`, `staticmethod` — and class bodies as scopes | 1443/1668 |
| with `match`, `del obj.attr`, slice objects, dict `**` spread, `@`, `async with` | 1469/1668 |
| with `ExceptionGroup` and `dir` | 1472/1668 |
| with `__init_subclass__`, `__class_getitem__` and generic aliases | 1474/1668 |
| with `ascii`, `sys.implementation` — and the harnesses no longer mangling non-ASCII output | 1478/1668 |
| with `__reversed__`, `__iter__` returning a non-iterator, and `reversed` on a set | 1481/1668 |
| with the numeric conversion dunders, `NotImplemented`, `raise` of a variable, and `sum` refusing strings | 1486/1668 |
| with cyclic `repr` and container identity in the interpreter | 1487/1668 |
| with live dict views, and the dunders on the builtin types | 1492/1668 |
| with `bytearray`, `memoryview`, `locals()`, `globals()`, `__builtins__`, `get_origin`/`get_args` and printf-style `%` formatting; with bytes comparing by CONTENT rather than by buffer address, every non-number kind comparing by identity rather than by a union member that happened to be a pointer, `typing` forms and builtin type thunks interned so two mentions are one object, and the interpreter rendering a container's elements through its own repr instead of Python's | 1503/1668 |
| with metaclasses: `__new__` dispatch, `object`'s defaults as callable values, `type` as a class you can inherit from, `__prepare__`, class keywords, `__instancecheck__`/`__subclasscheck__`, and the three-argument `type()` | 1507/1668 |
| with `bytes`/`bytearray` compared by content, `nan` inside containers, shadowed builtins, starred `print(sep=)`, unpacking arity, the deleted `except` target and one canonical object per builtin type | 1517/1668 |
| with `@v.deleter`, `__set_name__` and the class keywords `__init_subclass__` is configured by | 1520/1668 |
| with strings measured in CHARACTERS everywhere -- indexing, slicing, iteration and `find` all counted bytes | 1521/1668 |
| with `def` statements running where they are written, the exception hierarchy in `__mro__`, PEP 479, PEP 585 alias attributes, PEP 604 unions, generator methods as values, and `return` in a `finally` discarding | 1533/1668 |
| with PEP 649 lazy annotations, PEP 682's `z`, `complex("1+2j")`, class bodies in source order and `del xs[1:]` | 1540/1668 |
| with bytes methods, `bytes(n)`, the `__slots__` conflict and its member descriptor | 1543/1668 |
| with `__defaults__`/`__qualname__`/`__code__`, class annotations, chained assignment, a bare `return`, expression statements in a class body, `dir()`, the `%` mapping form, `ExceptionGroup.message`, positional-only enforcement and `x in gen` consuming | 1558/1668 |
| with `await` in an expression position, which `crash_scan.py` found producing invalid IR while the score called it a refusal | 1560/1668 |
| with `functools`, `itertools`, `contextlib`, `warnings` and `statistics` as BUNDLED PYTHON MODULES spliced into the program that imports them | 1566/1668 |
| with metaclass inheritance, `type.__call__`, `abc` and `enum` | 1570/1668 |
| with `collections`, `collections.abc`, `typing`'s classes, `fractions`, `decimal`, `tomllib`, `pathlib`, `dataclasses`, `contextvars`, `numbers`, `copy`, `types` and `os`'s path protocol -- and with the SPLICER no longer renaming a bundled module's own locals, `apy_native` no longer interning every builtin protocol method into whichever was built first, `hasattr` answering for a builtin's protocol at all, `encode`/`decode` doing real conversions, and a dict refusing to be resized under a walk | 1602/1668 |
| with multiple inheritance and a real C3 linearisation, `super(C, self)`, PEP 3151's exception hierarchy, PEP 563, PEP 695, `sys.monitoring`, audit hooks, the buffer protocol and provable arity errors raising instead of refusing | 1618/1668 |
| with `inspect.signature` rebuilt from `__code__`, `datetime`, `zoneinfo`, `annotationlib` and PEP 553 | 1622/1668 |
| with the Unicode character classes as a generated table, `range` as a lazy object, `yield from` STEPPING the inner generator rather than draining it, a call dispatch that goes past eight parameters, PEP 696, PEP 236 and PEP 594 | 1634/1668 |
| with PEP 560's `__mro_entries__` and the TypedDict key sets | 1637/1668 |
| with a class able to EXTEND A BUILTIN, and `del obj[k]` dispatching `__delitem__` at all | 1639/1668 |
| with `except*`, t-strings, an exception class that may have a BODY, a class body that is a BLOCK, `__anext__` on a class, the asyncio TASK LAYER (`create_task`, `cancel`, `wait_for`, `TaskGroup`) and a bundled `unicodedata` | 1648/1668 |
| `results/asmpython.json` — with PEP 657: a traceback that names a frame, a frame that names a code object, and `co_positions()` -- recorded per statement, and only for a program that asks | **1649**/1668 |

**EVERY REMAINING FAILURE CALLS `compile()`, `eval()` OR `exec()`.** Nineteen
cases, verified by reading each one rather than by inferring it from the
names: nothing else in the suite fails. The ceiling without a compiler in the
produced binary is exactly where the score now is.

Read the first two rows together. The suite's shim invoked
`python -m asmpython` without putting this checkout on the path, so it resolved
to whatever was installed in site-packages — a released build of the compiler
now in `archived/legacy/`, not the tree it sits in. Against the actual 3.14
compiler the score was zero, and for one reason: the frontend accepted only
function definitions at module level, and every conformance case is a script.

**Take a snapshot only when the tree builds.** A run against a tree that
briefly does not compile records a number with no meaning; one such run came
back 409 against a tree measuring 888 either side of it.

**And take it ALONE.** A measurement beside a test run is slower than both of
them in sequence and produces failures that are not in the code -- see
"Concurrent runs make the suite lie" below. `src/` is snapshotted, so a
measurement is safe from edits; it is not safe from another measurement.

## What the score does not measure

Most of what was found between 1389 and 1558 was NOT a missing feature. It was
code that ran and produced a confident wrong answer -- and the score barely
moved for any of it, because a wrong answer and a right one both count as one
case until someone looks.

The list below is long on purpose. Each entry cost real time to find, and every
one of them was invisible to the number this file is otherwise about.

Reachable from ordinary Python, all of them:

* `apy_as_float` read an int's bits as a double, so `f(42)` into a `float`
  parameter answered `4.15e-322`. Reachable from ordinary annotated Python.
* A bare `raise` inside `except` SWALLOWED the exception. The comment above it
  explained why that was safe, and described the one case that never happens.
* `raise e` where `e` is a variable built a new exception named after the
  variable, so the handler for the real type never fired.
* `__reversed__` and `__iter__` hooks were ignored, so a class got a sequence
  it never defined.
* `isinstance(exc, T)` was False whenever the type arrived as a value rather
  than a literal — a call-site rewrite was masking a gap in the general path.
* SIX harnesses decoded compiled-program output with the locale codec instead
  of UTF-8, so every non-ASCII case failed while the compiler was emitting
  exactly the right bytes.
* `b"ab" == b"a" + b"b"` was False. Bytes fell through to the NUMERIC
  comparison, which reads a union member that for them is the buffer pointer
  -- so two identical literals compared equal only because the backend emits
  one static buffer for both. Every dict lookup and `in` test on a computed
  bytes key went the same way.
* Strings were INDEXED, SLICED, ITERATED and SEARCHED by byte while `len`
  counted characters. Identical for ASCII and wrong for everything else, in
  both runtimes equally -- so `s[1]` on `"héllo"` was the first half of a
  character and a `for` loop walked off its own end.
* `__new__` was ignored entirely: the instance was allocated and the method
  never ran. Every `__new__`-based singleton and factory silently did nothing.
* A class attribute BOUND TO NONE was invisible to the interpreter, whose
  lookup answered None for both "absent" and "the value is None". Every
  lazily-filled slot starts as the latter.
* `print([P(1)])` did not print. The interpreter handed containers to Python's
  `repr`, which shows an address for anything this runtime defines and raises
  out of the bridge for a user `__repr__`.
* Keyword-only parameters were mis-modelled in THREE places, each differently,
  while the function value had carried the count correctly all along.
* Unpacking dropped surplus values in silence: `a, b = [1, 2, 3]` bound two and
  discarded the third.
* `return` inside a `finally` did not discard the exception it was cleaning up
  after, so the function answered and raised.

The conformance number moved for none of the locale-codec item and barely for
several of the others. `compare.py` per-case, the multi-path corpus, and hand-comparison
against CPython are what found them; the score is a poor proxy for correctness.

**Two runtimes must agree about identity.** The interpreter minted a fresh
handle per access for several things the C interns — the suspension token,
`apy_stop`, `NotImplemented`, and every mutable container. `xs[1] is xs` was
False in one path and True in the other. Handles are now recorded at creation,
which closes the general case rather than the four instances.

**A path that answers for kinds it was never told about is worse than one
that refuses.** `apy_eq_raw` ended with the NUMERIC comparison, which reads a
union member that for a non-number is a POINTER. So `b"ab" == b"a" + b"b"` was
False -- and `b"ab" == b"ab"` was True only because the backend emits one
static buffer for two identical literals, which is what hid it. Two slices
sharing a `start`, two views onto one dict and two memoryviews over one buffer
each compared EQUAL for the same reason. Every dict lookup and `in` test on a
computed bytes key went the same way. The fix is a guard, not four cases:
anything that is not a number is identity unless it opted in to content
equality.

**An invariant nobody re-checks is a claim, not an invariant.** This file said
"No case crashes" and it was false: `crash_scan.py` found one producing invalid
IR. `await a() + await b()` computed the left operand into a REGISTER, the
right operand suspended, and the resume path read a register no path had
written -- the same rule that puts a generator's locals in frame slots, missed
for every intermediate. Every display holding an accumulator across its
elements had it too. The harness records invalid IR as REFUSED, so the score
had nothing to say and the claim sat here unchallenged.

Worse, fixing it exposed a divergence between the runtimes: a frame slot in the
C holds a POINTER and sees a tuple grow in its cell, while the host stored the
OBJECT and froze at the empty tuple it went in as. One rule -- the slot holds
the handle -- and both agree.

`crash_scan.py` exists because the score cannot see this class of bug. RUN IT.

**Concurrent runs make the suite lie.** Three heavy runs at once -- a suite, an
integration pass and a conformance sweep -- exhausted the machine and produced
failures that are not in the code: QEMU timing out, and a compiled program
exiting with a Windows resource code. The integration pass took 1365s beside
two others and 305s alone. Every such failure this session was contention, and
the first one was misdiagnosed as an AArch64 codegen bug. RUN THEM ONE AT A
TIME, and when one fails, rerun it alone before believing it.

**A patch that lands in the wrong function still applies cleanly.** During the
bytes work an equality body ended up inside `apy_add`'s mixed-operand branch,
where it returned a C int as an `apy_value` -- so `b"a" + "a"` segfaulted
instead of raising the TypeError that is the whole point of PEP 3112. The
`sub()` helper asserts the anchor is UNIQUE, and it was: unique and wrong. The
corpus never added a bytes to a str, so nothing caught it until the case did.
Anchors should be chosen inside the function being changed, and a corpus entry
should say what a change is NOT allowed to break as well as what it fixes.

**Fifty-two call sites, or one.** bytes methods looked like a per-method
change: `apy_str_self` gates fifty-two of them, and each returns through its
own `apy_str_take`, so each would have to re-tag its own result. The estimate
written down here said "per method, not central". It was wrong. The method
dispatch chooses the symbol in ONE place, so wrapping the call there --
`apy_str_like(receiver, result)`, which re-tags a str result and the str
elements of a sequence result and leaves an int alone -- covered every method
at once. The receiver check and four argument checks had to learn the kind;
nothing else did. Estimate the hook, not the surface.

**The oracle was not compiling what a script compiles.** The multi-path corpus
runs each program under CPython through `exec(compile(src, ...))`, and
`compile` INHERITS `__future__` flags from its caller -- the test module says
`from __future__ import annotations`, so every corpus program was compiled with
annotations stringified, a language mode a script does not use. The reference
disagreed with CPython about any program reading `__annotations__`, and
disagreed silently: the corpus simply had no such program until now.
`dont_inherit=True` is the fix. An oracle inherits its context unless told not
to, and the context here is a test file that has nothing to do with the
programs it is judging.

**One list, two questions.** Module-level `def`s were filtered out of the
entry's body, and that same filtered list decided WHETHER there was an entry at
all. Putting the `def` statements back -- so their defaults and decorators run
where they are written, as Python evaluates them -- silently changed the second
question too, and a module of pure definitions grew an entry it should not
have. 403 tests failed at once, which is the good case: a list serving two
purposes fails loudly the first time they diverge. The two are separate
variables now.

**A limitation written down is still a bug.** Two of the biggest finds this
stretch were sitting in comments that described them accurately and called them
acceptable. `apy_raw_len` returned a str's length in BYTES while `apy_len`
returned CHARACTERS, and the comment said the two disagreeing was a documented
limitation -- but a `for` loop takes its bound from one and its elements from
the other, so every string holding a non-ASCII character walked off its own
end, and `s[1]` was the first HALF of a character. `_dyn_unpack` said in its
docstring that the arity was not checked and that every use in the suite had
the right arity; a short sequence reported an IndexError from a subscript the
program never wrote, and a LONG one bound the leading names and dropped the
rest in silence. Both were identical to CPython for the easy input, which is
what kept them comfortable. A comment is not a test, and "wrong only for the
cases we don't have" is a prediction, not a property.

**"A redesign" was the right diagnosis and the wrong conclusion.** Metaclasses
were written down here as needing `class` lowering rebuilt. What they actually
needed was one missing idea: a callable the RUNTIME owns. Every callable was
compiled code, so `object`'s defaults existed as behaviours with no VALUE
naming them -- which is why `super().__init__()` in a class with no explicit
base was an AttributeError, and why `type` could not be a base class. With a
native callable, `type` became an ordinary class object whose dict holds
`__new__`, `class Meta(type)` got a real base, and the metaclass protocol fell
out of the paths that already existed. The lowering change was one branch:
run the body into a mapping instead of into the type, then call the metaclass.

Two things it did surface on the way, both silent wrong answers reachable
without any metaclass: `__new__` was IGNORED -- the instance was allocated and
the method never ran -- and a class attribute BOUND TO NONE was invisible to
the interpreter, because its lookup answered None for both "absent" and "the
value is None". Every lazily-filled singleton slot starts as the latter.

**Removing an accident can uncover a rule that was never written.** Bound
methods compared equal because a function fell through to the numeric path,
which read the code pointer -- so `c.m == c.m` was True and `c.m == d.m` was
True too. Fixing the general fall-through broke the first and fixed the
second, and the conformance case that noticed was the one measuring the half
that had been right. CPython's actual rule -- same function AND same receiver,
and only for bound methods -- is now written down in both runtimes.

**One rule stated in three places will be got wrong in some of them.** A
keyword-only parameter takes no argument POSITION. The arity check, the call
lowering and the runtime binder each restated that, and each got a different
part wrong: `b(1, 2, c=9)` was refused as two values for `c`, the `2` landed
in `c` instead of `*args`, and a keyword naming a keyword-only parameter went
into `**kw` whenever there were surplus positionals. The function value has
carried the count all along (`apy_func_kwonly`) -- what was missing was any of
the three consulting it.

**The same job done twice will be done differently.** The interpreter rendered
a container by handing it to Python's `repr`, on the reasoning that Python
could not disagree with CPython about `[1, 2]`. It disagreed about every
element the runtime defines: a builtin type printed as an address, and a user
instance raised out of the bridge, so `print([P(1)])` did not print. The C had
recursed through its own renderer from the start.

**A new object kind has to be taught every generic path.** `slice` and the dict
views each needed `repr`, `len`, iteration, membership, subscripting and the
operators — in the C, then again in the interpreter. The ones missed did not
fail loudly; they answered.

## The shape of the frontend

Two paths, and a function's annotations decide which it takes:

* **Static** — every parameter annotated `int`/`float`/`bool`/`None` AND a
  return annotation. Machine words, no allocation. This is the original
  annotated subset, and every pre-existing test uses it.
* **Dynamic** — the module's top-level statements, and any function with an
  unannotated or `object`-annotated parameter, or no return annotation. Every
  value is a runtime object carrying its own type; every operation is a call
  into `link/objects.py`. This is the path ordinary Python takes.

One representation per value for its whole life, with exactly one boundary
(`_dyn_call`). That is deliberate: the suite's `TAXONOMY.md` names
"representation follows the slot, not the value" as the dominant defect of the
compiler this replaces.

## How to work on this

**Measure with `compare.py`, never the raw score.** A run that fixes three
cases and breaks three moves the number by nothing and the tree by a lot. Every
checkpoint in the table above was gated on a per-case comparison showing no
surviving regressions.

**`src/` may be edited while a run is in flight.** The test runner and the
conformance harness both snapshot it first -- see `tests/harness/snapshot.py`
-- so a run measures one tree from start to finish. `--live-src` opts out and
says why you should not.

**Every feature lands with a corpus entry.** `test_dynamic_python.py` runs each
program under CPython, the IR interpreter and the C backend and compares all
three -- 104 programs now. It has caught every divergence between the two
runtimes this session, and no conformance case would have caught any of them.

Write the entry to say what the change is NOT allowed to break as well as what
it fixes. The bytes work re-tagged every method result and broke `b.hex()`,
which answers a str; the same work put an equality body in `apy_add` and made
`b"a" + "a"` segfault. Both were one line of corpus away from being caught the
moment they happened.

**A CASE PER PROCESS CANNOT SEE INTERACTION.** Every case gets a fresh process,
so anything the runtime keeps for the length of one program is fresh in every
case -- and the five per-host-state bugs above could not have failed here,
however many cases ran. `--merge N` compiles N cases into ONE program, which is
where they can.

IT DOES NOT REWRITE THE CASES, because `cases/` is the oracle and a merged
program built by renaming what a case declares would be testing something the
suite does not contain. Batches are chosen so renaming is unnecessary.

AND THE RULE FOR CHOOSING THEM IS NOT "DISJOINT NAMES", which is what it looks
like it should be. A case that passes ALONE never reads a module-level name
before binding it -- if it did, CPython would raise NameError, and every case
passes under CPython. Concatenation preserves order, so a neighbour rebinding
`a` cannot change what a later case computes: the later case binds `a` first.

What that argument does not cover is a case that asks the namespace ABOUT
ITSELF -- `globals()`, a bare `dir()`, a `try/except NameError` whose whole
point is whether a name is bound -- or `del`, which is how a case makes a name
unbound on purpose. There are 28 of those in 1675, and they keep the
conservative disjoint-names rule among themselves; they never share a batch
with a free case, because a free case's binding is exactly what one would see.

THE DIFFERENCE IS THE WHOLE MODE. Disjoint names packs 3.4x -- `a`, `b`, `r`,
`xs` and the 392 cases sharing the `_moved`/`move`/`_original` idiom set that
ceiling, and raising the batch size past 8 buys nothing against it. The free
pool packs to whatever size is asked: 23x at 32, 37x at 64.

A MISJUDGEMENT HERE COSTS A RE-RUN AND NOT A WRONG VERDICT, which is what makes
the permissive rule safe to take. Whatever a batch does not settle is re-run
case by case, so the worst it can do is spend the time it was saving.

AND A BATCH IS NEVER THE LAST WORD. A marker printed BETWEEN cases splits the
output into per-case segments, each compared exactly as a solo run compares it,
and whatever a batch does not settle is re-run alone. A case that then passes is
reported as a MERGE-ONLY DIVERGENCE -- an interaction bug, and the result this
mode exists to produce.

THE FIRST VERSION COMPARED THE CONCATENATION instead, and reported three sound
cases as divergences. The harness rstrips a case's output, so
`text/str/case-conversions` -- which ends with `print("")` -- has a trailing
blank its `# expect:` block cannot hold. True and harmless alone; wrong the
moment another case's output follows, and it failed that case's two batch-mates
with it. A merged verdict has to be reached the same way a solo one is, or the
mode invents the bugs it is meant to find.

**Compare against CPython, not against the case.** Nearly every serious finding
came from running a wider program than the case and diffing the whole output.
The case tells you one line is wrong; CPython tells you which rule you broke.

## What is left

29 counted cases at the 1639 measurement, of which TEN were reachable and
nineteen call `compile()` or `eval()`. **All ten are now closed**, each
verified against CPython through all three execution paths:

    async/cancellation-raises-cancellederror     the task layer
    async/task-group-collects-results            `TaskGroup`
    async/wait-for-timeout                       `wait_for`
    datamodel/async-iterator-protocol            `__anext__` on a class
    exceptions/custom-exception-with-init        an exception with a body
    pep/0654-exception-groups/except-star        `except*`
    pep/0750-template-strings/t-string-structure t-strings
    quirks/class-body-invisible-to-comprehension a general class body
    text/str/unicode-normalization               a bundled `unicodedata`
    pep/0657-fine-grained-error-locations         `co_positions`

**AND THEN `compile()`, `eval()` AND `exec()` TOO**, which was the last block:
nineteen cases, and the whole of what was left.

WHAT THEY ACTUALLY NEEDED, which is narrower than the names suggest.
EIGHTEEN of the nineteen call `compile()` alone -- they ask whether source is
valid Python and read the answer -- ONE calls `eval()`, and NONE calls
`exec()`. That is a parser and a validator, not a compiler, and the split is
what made the work tractable:

    _pylex      text   -> tokens
    _pyparse    tokens -> tree
    _pyvalidate tree   -> the same tree, or the SyntaxError CPython raises
    _pycompile  the three of them, behind the name a program calls
    _pyrun      `eval` and `exec`, walking the tree `_pycompile` built

THE SPECIFICATION WAS READ OUT OF THE CASES rather than transcribed. Every
`syntax/*` case is a list of sources and whether `compile()` takes each; a
script pulled all 87 probes off their trees and compared three answers --
accepted or not, which exception CLASS, and which warnings -- against
CPython's own `compile`. `test_bundled_compile.py` keeps doing that, from the
cases, so the two cannot drift.

`_pyvalidate` IS THE HALF A GRAMMAR CANNOT DO. `break` outside a loop,
`return`/`yield`/`await` in the wrong scope, a duplicate parameter, a reserved
name bound, `global x` after `x = 1`, `nonlocal` with no binder, two starred
targets, `**rest` before the end of a mapping pattern. Every one depends on
what ENCLOSES the statement, which is why `_pyparse` accepts them all and the
parser test asserts that it does.

WHAT `eval` AND `exec` RUN is a tree walk, not a bytecode VM. portapy is one --
2,400 lines of VM plus a 1,150-line frontend that compiles CPython's `ast`,
which does not exist inside a produced binary, so that half would have to be
rewritten against this tree. Every program calling `eval` would carry it. The
walk is 400 lines and the entry points are `_pyrun.eval`/`_pyrun.exec`, so
that engine can replace this one without anything above noticing.

E0091 IS NOW W0091, a warning with two messages, because the costs differ:
`compile()` answers validity through the bundled parser rather than through
the compiler that built the binary, and `eval`/`exec` INTERPRET what they are
given. Reported where the call is written, before the splice consumes the
name.

AND IT COSTS NOTHING UNTIL ASKED. `_pycompile` is spliced because the NAME
appears -- no import brings a builtin in -- so a program that never writes one
carries none of it. Measured: no `_pycompile` function in the module, and no
diagnostics. A program that DOES write one pays 22 seconds of compile time for
two and a half thousand spliced lines, which is why the corpus program that
covered this moved to `test_runtime_compiler.py`: it took a corpus run from
two and a half minutes to over six, and the corpus is the loop to stay inside.

FIVE PARSER BUGS came out of the spec table -- `class C(*bases)`, `match(1)`
as a call, `(x := 1)` as a statement, a second `*` in a parameter list, and
two unexpected-indent shapes that have to report `IndentationError` rather
than a plain refusal, because `except IndentationError:` is what a program
writes.

AND FIVE MORE THAT WERE NOT ABOUT THIS AT ALL:

* `raise make_error()` built an exception NAMED AFTER THE FUNCTION rather than
  raising what the call answered. Every call in a `raise` was read as "build
  one of these".
* A BUILTIN HELD AS A VALUE THAT FAILED KILLED THE PROCESS. The synthesised
  thunk was lowered with whatever `info` the last real function left behind,
  so one emitted after the entry inherited "a failure here stops the process"
  -- and `f = int; f("x")` was fatal instead of a ValueError the caller could
  catch. `_Synthetic` is the fix, and it exists for one field.
* THE SPLICER RENAMED A METHOD that shared a name with a module-level
  function: `_pyrun` has a top-level `eval()` and a `_Walker.eval`, and the
  class lost the method it plainly defines. The same trap `_bound_locally`
  closed for function locals, one level up.
* AN EXCEPTION SUBCLASS WITH AN EMPTY BODY never ran its base's `__init__`:
  the fast path binds no class, so there was nothing for `apy_make_exc` to
  find. `class IndentError(LexError): pass` came back without the attributes
  its base sets.
* A PROGRAM'S OWN `self.value` WAS SHADOWED by the `value` the exception kind
  offers -- which belongs to StopIteration and ExceptionGroup in CPython and
  is answered for every exception here. `_Returned(42).value` gave back the
  message. What the program stored now wins, except for `args` and the
  dunders, which really are BaseException's.

WHAT THE TEN ACTUALLY COST, since the case names understate all of them:

* **`except*`** is not `except` with a flag. Every clause runs, each holding
  the part of the group its type matches, so the question is "how does this
  group divide" rather than "which handler". `apy_group_dispatch` answers it
  in one call and the lowering reads a tuple -- one entry per clause and the
  leftover last, which is what has to be re-raised.
* **t-strings** exist so that nothing is joined. `Template` keeps `strings`
  one longer than `interpolations` by construction, and the conversion and
  format spec are RECORDED rather than applied -- applying them would make it
  text, which is what a template exists not to be.
* **an exception class with a body** was the largest. A user exception was a
  NAME in a hierarchy, which has nowhere to put an `__init__`; it is now both,
  and the exception cell carries a class and a dict. `super().__init__(msg)`
  reaches `BaseException.__init__` through a native that the super lookup
  falls back to AFTER walking the base chain -- intercepting it before sent
  every subclass straight past its own base's `__init__`.
* **a general class body** made a class body what it is: a block that runs.
  Bindings from an `if` or a `try` go into the class NAMESPACE rather than a
  register, because two branches both write and a later read has to find
  either. And a comprehension inside one cannot see it -- except its outermost
  iterable, which is the rule the case is actually about.
* **the asyncio task layer** is a scheduler. `create_task` hands a coroutine
  to the loop, which runs it in the gap wherever anything else suspends;
  `cancel()` ASKS, and the exception arrives at the task's next suspension
  point, where a `try` around the `await` inside it can catch it. `wait_for`
  is a deadline among the others on the virtual clock, and `TaskGroup` is an
  object with natives for `__aenter__`, `__aexit__` and `create_task`.
* **`unicodedata`** is a BUNDLED module, not runtime C: `unicode_table.py` is
  spliced into every binary and the normalisation data is twice its size,
  while almost no program wants it. Generated from CPython by
  `_gen_unicodedata.py`, and it agrees with CPython on all four normalisation
  forms, `category`, `combining` and `decomposition` for every one of the
  1,114,112 code points.
* **`co_positions`** needed a traceback that is a real object: it names a
  FRAME, which names a CODE OBJECT, which knows where its operations were
  written. None of the three existed, and `e.__traceback__` was an empty
  tuple standing in for one.

  ONE POSITION PER STATEMENT is what the frontend has -- `_dyn_stmt` sets a
  span and nothing finer is tracked -- so `co_positions()` answers one
  four-tuple per statement of the function rather than one per instruction.
  Coarser than CPython's table and made of the same kind of fact, which is
  the difference worth stating rather than papering over.

  AND IT COSTS NOTHING UNLESS THE PROGRAM ASKS. Recording is a call per
  statement, so the frontend emits none of it unless the source mentions
  `__traceback__` or one of the attributes reached through one -- measured:
  zero `apy_at` instructions for a program that does not, and the table
  function is not emitted at all.

  ONE FRAME DEEP, because there is no call stack. `tb_next` is None and the
  frame named is the INNERMOST one -- where the exception actually came from
  -- where CPython's `e.__traceback__` is the outermost and walks inward. For
  a raise in the same frame as the `try`, which is what the case tests and
  most of what programs read, the two agree exactly: the corpus program
  `traceback_positions` holds four real line numbers against CPython.

  IT ALSO FIXED A WRONG ANSWER: `ValueError("x").__traceback__` was a tuple,
  which is not None -- so a program telling a caught exception from one it
  merely built got the wrong answer. An exception now carries a position only
  once `raise` has given it one.

WHAT THE WORK FOUND, which is the part no case would have named:

* **A SUSPENSION IN AN EXPRESSION DESTROYED EVERYTHING COMPUTED BEFORE IT.**
  `log.append(await f())` produced invalid IR, and so did `f(await a(), await
  b())`, `xs[await i()]`, `a < await b()`, `f"{await a()}{await b()}"`, `n +=
  await f()`, `f(*[await a()])` and a call with an `await` in a KEYWORD value
  -- that last one silently, because the argument buffer is a stack slot and
  a suspension returns through the step function, so the callee was handed
  whatever the C stack held.

  All of it was the same fact: a register does not cross a suspension, which
  is why a generator's locals live in frame slots. Only three places knew --
  a binary operand, a dict key, and a display being filled. `_dyn_operands`
  and `_spill_across_await` now cover every position, and `_dyn_arguments`
  holds READERS while the arguments are evaluated rather than registers.

  AND `yield` IS A SUSPENSION TOO, which the check had never asked about: it
  looked for `ast.Await` alone, so every one of the shapes above was also
  broken for `(yield)` in an ordinary generator. So is an `async for` INSIDE
  A COMPREHENSION, which contains no `await` node at all -- `out.append([x
  async for x in ag()])` was refused for that reason and nothing else.
  `_suspends` asks about all four now: `Await`, `Yield`, `YieldFrom`, and a
  `comprehension` marked async.

  A SLICE BOUND NEEDED ITS OWN SPILL. `xs[await a():await b()]` holds an
  `i64` from `apy_index` rather than a handle, and the frame's object slots
  read back as objects -- so `_spill_raw` uses the raw pair the generator
  frame already has.

  Four corpus programs hold every position against CPython on all three
  execution paths: `await_in_every_expression_position`,
  `yield_in_every_expression_position`,
  `await_in_slices_specs_and_asserts` and `generators_and_coroutines_mixed`.
  AND FORTY-FOUR SHAPES ARE PINNED in
  `tests/asmpython/unit/test_suspension_positions.py`, which asks only whether
  the frontend produced IR the verifier accepts. No backend runs, so the whole
  sweep is two seconds and can be run beside a measurement -- which is what
  makes it the right net for this bug class, where the corpus's three paths
  and C compile are more than the question needs.

  WHY NOTHING CAUGHT IT: the conformance suite's async cases await on a line
  of their own, which is the one position that works. The failures were
  REFUSALS rather than wrong answers, so they were invisible to the score --
  a program that does not compile is not a case that fails.
* An error check after an exception constructor tested the sticky FLAG rather
  than the call's own result, and `try: raise A / finally: raise B` builds B
  with A's flag already set -- so the `raise B` went to the handler carrying A
  and the exception the `finally` wrote was silently dropped. The corpus
  caught this one within a minute of the change that introduced it, which is
  what a corpus is for.
* `Instance._send` in the interpreter refused a NATIVE, so a class the RUNTIME
  builds -- `asyncio.TaskGroup` is one -- had every protocol method report as
  absent, and `async with TaskGroup()` awaited `NotImplemented`.
* THE POSITION TABLE WAS MODULE-LEVEL IN THE INTERPRETER, and that is the
  trap `_types`, `_forms` and `user_exc` are all on the host to avoid. The C
  keeps it in file statics and each compiled program is its own process, so
  one table is one program; `objects_host.py` runs MANY programs in one
  process, and the second one indexed into the first's rows. It showed as a
  traceback reporting a line six too small -- a wrong answer, not a crash --
  and it produced exactly one flaky corpus failure that a rerun did not
  reproduce, because whether the two runs shared a worker decided it.

  THE TASK REGISTRY HAD IT TOO, found by looking rather than by failing: a
  shared list would let the second program step what the first left
  unfinished. Both are on the host now, and running each probe TWICE in one
  process -- which is what the corpus does and what produced the flake -- is
  the check that says so.

  ANYTHING A COMPILED PROGRAM KEEPS IN A FILE STATIC BELONGS ON THE HOST.
  That is the whole rule, and an audit of the file found one more predating
  this session: `_LIVE_AGENS`, which let one program's `asyncio.run` close an
  async generator another had left suspended. On the host now.

  WHAT REMAINS SHARED and is fine: `_NOW` and `_WAKE`, the virtual clock. It
  is monotonic and every deadline is relative to it, so a second program
  starting at a later moment measures the same intervals.

`*` AND `**` ONTO SOMETHING THAT IS NOT AN ORDINARY FUNCTION was its own
small family, and all three were WRONG ANSWERS rather than refusals:

    max(*xs)               TypeError: 'int' object is not iterable   FIXED
    "{}-{}".format(*xs)    AttributeError: no attribute 'format'     FIXED
    dict(**d, b=2)         invalid IR: unknown global 'gv_dict'      FIXED

`_dyn_spread_call` reaches the callee as a VALUE, and neither of the first two
is one: `max` as a value is a one-argument thunk that SCANS an iterable, so
two spread arguments handed the scan an int; and `str.format` is not a value
at all, being chosen by NAME at the call site. `max(*xs)` is now lowered as
`max(xs)` -- the same question, and `max(a, b, c)` is defined as the largest
of `[a, b, c]`, so the two cannot differ -- and `format` is recognised in the
spread path exactly as `_dyn_method` recognises it. `spread_calls_onto_every_
callable` holds both against CPython on all three paths.

THE THIRD WAS TWO BUGS. The `**` sends the call through the value path, and
`_dyn_load` has no notion of a builtin as a value -- so it read a module
global named `dict`, which no program defines, and the verifier refused over
`gv_dict`. Giving it the thunk then reported `dict() got an unexpected keyword
argument`, because a builtin's value form collects POSITIONAL arguments and
there is no thunk shape for one taking `**kw`. `dict(a=1, **other)` IS the
keyword mapping, so it is built where the call is written, in source order --
which is what makes `dict(**d, k=1)` and `dict(k=1, **d)` different dicts, as
they are in CPython.

The compile block is bigger than it looks from the case names: `syntax/*`
tests error detection by calling `compile()` on a bad program, and so do a
dozen PEP cases. Nothing else is a block at all -- the stdlib group that used
to be second-biggest is gone, and so are the class-machinery, typing and
Unicode groups.

REGENERATE THIS from `results/asmpython.json` rather than trusting it. The
split above is one line of Python:

    python - <<'EOF'
    import json, pathlib, re
    d = json.loads(pathlib.Path("conformance/results/asmpython.json").read_text())
    for k, v in sorted(d["cases"].items()):
        if v.get("tier") in ("spec", "cpython") and v["status"] != "PASS":
            src = (pathlib.Path("conformance/cases") / (k + ".py")).read_text()
            print("compile" if re.search(r"(compile|eval)[(]", src)
                  else "       ", k)
    EOF

The counts on each item are from the last measurement that touched it, so read
them as an order of magnitude rather than a total. Regenerate the breakdown
from `results/asmpython.json` rather than trusting this list: it goes stale
every time something lands, which is how it should be.

* **`compile`/`eval`** (19) — DONE. The cases call `compile()` on a bad
  program and expect a `SyntaxError`, which needs a Python parser inside the
  produced binary. There is one: `_pylex`, `_pyparse` and `_pyvalidate`,
  bundled Python spliced into the program that names `compile`.

  There is a route that is not "write a parser in C": the bundled-module
  mechanism compiles PYTHON into the program, so a recursive-descent parser
  written as a bundled module would be compiled by this compiler and reach the
  binary that way. It is still one to two thousand lines and it has to agree
  with CPython about which programs are ill-formed, which is the hard half --
  but it is the same trade the stdlib modules already took, and the constraint
  that makes it honest is the same one: a bundled module may only use what
  this compiler accepts.

  Declining it would have capped the achievable score at 1649/1668, about
  98.9%. It was not declined.

  THE WORRY THAT `TestNoRuntimeCompiler` EXISTED FOR was a half
  implementation that accepts the call and answers something plausible --
  which nothing else in the suite would notice, because a case expecting
  `SyntaxError` and getting `accepted` reads as one more failure among the
  nineteen. What answers it now is `test_bundled_compile.py`: 87 probes read
  out of the cases themselves, each compared with CPython's own `compile` on
  the outcome, the exception CLASS and the warnings. Agreement is checked
  rather than assumed.

  Writing it found one: as a VALUE -- `f = compile`, `print(eval)` -- the name
  compiled and then raised `name 'eval' is not defined` at run time. That is a
  false statement about a name Python defines, and a runtime failure where the
  compiler already knew the answer. All three now report E0091 in both
  positions, with a message that says what is actually missing.
* **the standard library** (~35 across 14 modules) — `typing` 9, `functools`
  6, `collections` 6, `warnings` 4, then `asyncio`/`contextlib`/`abc` 3 each
  and ones and twos below that. The module table takes a nested namespace now
  (see `("ns", ...)`), so the SHAPE is solved and what remains is each
  module's behaviour.

  BUILT, for `functools`, `itertools` and `contextlib`: see
  `frontends/python/bundled.py`. A module written
  in Python is SPLICED into the program that imports it, under names it cannot
  collide with, and every reference rewritten -- so there is no import system,
  no module object, and one module by the time anything else looks. Adding
  `collections`, `contextlib` and the rest is now writing Python, not C.

  The constraint that makes it honest: a bundled module is compiled by THIS
  compiler, so it may only use what this compiler accepts. `functools` found
  two bugs on its first day -- a nested function capturing `*args` got None,
  and `f(*xs, **kw)` dropped every keyword -- neither of which any conformance
  case had reached. A construct a bundled module cannot use is a gap worth
  closing, not a reason to drop back to C.

  BUILT SINCE, and the list is now the interesting part rather than the
  mechanism: `abc`, `enum`, `collections`, `collections.abc`, `typing`,
  `fractions`, `decimal`, `tomllib`, `pathlib`, `dataclasses`, `contextvars`,
  `numbers`, `copy`, `os` (its path protocol only), `statistics`, `warnings`.

  A MODULE MAY BE BUNDLED IN PART. `typing`'s special forms are already
  runtime values and only its classes needed writing, so `splice` keeps the
  import of every name the bundled file does not define and lets it reach the
  builtin module table as before. That is what makes an incremental module
  possible at all.

  WHAT IT CANNOT REACH: `collections` is written as classes that HOLD a
  builtin rather than inherit from one, because `class D(dict)` is still
  refused. Everything a program DOES with a `Counter` works; `isinstance(c,
  dict)` is False where CPython says True, and `namedtuple`'s
  `isinstance(p, tuple)` is the one conformance line still failing for it.
  Closing that needs builtin subclassing, which is a real feature and not a
  workaround away.

  THE SPLICER HAD A SILENT MISCOMPILE, worth remembering because nothing
  caught it but a wrong answer: `_Rename` renamed EVERY `Name` matching a
  module-level definition, including a function's own local of the same name.
  `dataclasses` has a local `fields` list and a module-level `fields()`, and
  the local was rewritten to point at the function. It compiled and did
  something else. `_bound_locally` now computes what each body binds.
* **subclassing a builtin** — DONE, in the shape the entry predicted: a class
  records which builtin kind it extends, an instance of one carries a value of
  that kind, `isinstance` consults it, and `__getitem__`/`__setitem__`/
  `__delitem__`/`__len__` delegate to it where the class defines no dunder.
  `__missing__` is asked before the KeyError, which is what a `dict` subclass
  is usually for.

  WHAT IS NOT DONE, and it is the constructor: `tuple.__new__` is not called,
  so `class P(tuple)` gets an EMPTY tuple and has to fill its own storage --
  which is what the bundled `namedtuple` does. CPython would reject that class
  (`tuple expected at most 1 argument`) and this accepts it, so the two differ
  in what they ALLOW rather than in what they compute. A corpus program cannot
  cover it for that reason; the conformance case can, because it only uses the
  shape both accept.

  FOUND ON THE WAY: `del obj[k]` never dispatched `__delitem__` at all. A class
  that wrote one had it ignored and the delete was reported as unsupported --
  a wrong answer about the class's own method, in both runtimes, with no case
  reaching it until now.
* **a builtin as a value takes ONE argument** (0 cases) — `sorted(xs, key=len)`
  works and `reduce(max, xs)` does not: the synthesised thunk's arity is baked
  into its shape. Every value-form builtin is one-argument for that reason, and
  a two-argument one has no thunk to be. Wants a variadic thunk shape, which
  `print`/`dict`/`bytes` already have as a special case -- generalising that
  is the fix.
* **provable arity errors** — SETTLED, and the split is per-path rather than
  per-project. A DYNAMIC function's mismatch is now W0053 and a runtime
  TypeError: Python's answer is a TypeError a program may catch, and while
  this was a compile error such a program could not be compiled at all. A
  STATICALLY TYPED function keeps E0053, because it has a fixed machine
  signature and genuinely cannot be called wrongly -- which is also the only
  suite test that asserted the code, so it still passes.

  The mechanism worth remembering: the check does not just change severity, it
  records the call node in `late_arity` and lowering routes that call through
  the VALUE path. The direct path hands the symbol a count it cannot accept,
  which is a C compile error rather than a Python one.
* **mutation during iteration** — DONE, and cheaper than this entry feared.
  The cursor object already exists (`APY_ITER_K`), so the size the walk
  started with is a field on it and the check is one comparison inside the
  step that already reads the length. Recorded only for a dict: a list may
  grow or shrink under a walk and CPython allows it, so recording the size for
  one would refuse a legal program.
* **multiple inheritance** — DONE. A class records its bases and the C3
  linearisation of them (`apy_c3` in the C, `c3` in the host), every lookup
  walks that order, and `super()` walks the RECEIVER's order past the class
  the method was defined in -- which is the part that makes a diamond work:
  inside B's method on a D instance, `super()` must reach C and not A, and
  only the receiver's order knows C sits between them.

  A class with ONE base keeps the base-chain walk it always had. That is not
  an optimisation but a safety property: `mro` is None there, so nothing about
  the single-base path changed and the cases that exercised it cannot have
  moved.
* **`e.args` on a runtime-raised exception** (0 cases) — a failed operation
  keeps a type and a message TEXT, never the object, so `except KeyError as e`
  rebuilds one and `e.args[0]` is the repr of the key rather than the key.
  `repr(e)` is right now; `args` needs the key retained alongside the message.
  No case tests it, which is exactly why it is written down.
* **a user exception class WITH A BODY** (2) — `class AppError(Exception)`
  with an `__init__` that sets `self.code`. Exceptions here are a NAME
  hierarchy, not type objects: `raise` and `except` match on the name, which
  is why the hierarchy is a table and why a class with a body is refused
  outright. Supporting one means an exception instance carrying a dict and its
  class's `__init__` running at the `raise` -- the tension is real and the
  design decision (keep the name table and give `APY_EXC_K` a dict, or make
  user exception classes ordinary types) belongs to whoever picks it up.
* **the asyncio task layer** (4) — `create_task`, `Task.cancel`,
  `Task.result`, `wait_for` with a timeout, and `TaskGroup`. `run`, `sleep`
  and `gather` exist; what is missing is a TASK OBJECT and the scheduler
  operations over one.

  This is the largest remaining block that is not `compile()`, and the four
  cases all need the same thing: something that owns a coroutine, holds its
  result, and can be cancelled -- where cancellation means raising
  `CancelledError` AT the suspension point, which is the same machinery
  `gen.throw` already has. `wait_for` needs the scheduler to notice a deadline
  it is already tracking for `sleep`.
* **`async for` over a USER CLASS** (1) — `__aiter__`/`__anext__` on an
  ordinary class. Only async GENERATORS are accepted. The awkward part is not
  calling `__anext__`; it is that the coroutine it answers may SUSPEND, and
  `apy_agen_step` would then have to hand the token outward and find the same
  in-flight coroutine again on resume -- so it needs somewhere to stash it,
  on the instance. Driving it to completion inline is the shortcut, and it is
  wrong for any `__anext__` that actually awaits.
* **`range` as an object** — DONE. `APY_RANGE_K` carries start/stop/step and
  every question about one is arithmetic on the three: `len`, indexing,
  membership, `index`, `count`, slicing (which answers a RANGE) and equality.
  `10**11 in range(10**12)` is a division.

  Two rules worth keeping in mind for the next kind like it. Equality is
  about the ELEMENTS and not the numbers -- `range(0, 3, 1) == range(3)` and
  every empty range equals every other -- and the host uses Python's own
  `range` rather than a class of its own, so the two paths cannot drift about
  any of it.

  WHERE THE WORK ACTUALLY WAS: not the kind, but the dozen places that listed
  the walkable types by hand. A new kind has to be added to every one of them,
  and the ones that were missed each surfaced as a plain "not iterable".
* **the Unicode character classes** — DONE, and embedded ALWAYS. The table is
  2948 runs generated by `link/_gen_unicode.py` from the reference
  implementation's own answers, spliced into the C at a marker so
  `link/objects.py` stays readable. Roughly 60KB of source and a binary search
  per character.

  The predicates now walk CODE POINTS rather than bytes, which is the half
  that was silently wrong: a multi-byte character was asked about its own
  continuation bytes, and those belong to no class, so every non-ASCII string
  answered False. And `isdecimal`/`isdigit`/`isnumeric` are three different
  questions again -- U+00B2 is numeric and not decimal, U+2167 is numeric and
  not a digit -- where they had shared one test.

  ONE TRAP IN GENERATING IT, and the corpus is what caught it: the TITLE bit
  must come from the CATEGORY `Lt` and not from `str.istitle()`. A one-
  character string of a capital letter IS titlecase to `istitle`, so asking it
  per code point gave every accented capital the TITLE bit -- and `isupper`
  treats a titlecase character as disqualifying, so `'É'.isupper()` answered
  False while `'ABC'.isupper()` stayed True because ASCII is decided in code.

  WHAT IT DOES NOT BUY: `unicodedata.normalize`. NFC/NFD needs decomposition
  data, which is a different and much larger table.

  The superseded note, kept because the reasoning still applies to the next
  table someone is tempted to approximate: approximating a few ranges is the
  one option to reject -- it
  would be wrong for characters nobody thought to list, silently.
* **the wrong-answer tail** (20) — the bucket that produced every serious
  finding this session. A refusal scan cannot see any of it; only running the
  cases and diffing against CPython does. It was ~60 at 1487 and is 20 now,
  which is the only number here that has fallen because the bucket was WORKED
  rather than because a feature landed.

  What is left in it is no longer a tail of unrelated ones. Four are the items
  listed above (`range`, `async for` over a class, TaskGroup, the Unicode
  classes); three want `inspect.signature` or a traceback with column
  information; two are `sys.monitoring` and audit hooks, which are runtime
  services a compiled program does not have; two are `__future__` flags. The
  genuinely loose ones are `yield from` not forwarding `send`, dict mutation
  during iteration, and `print` reporting itself as a `function` rather than a
  `builtin_function_or_method`.

## What a session of this shape actually costs

Written down because the numbers decide how to work, not because they are
interesting.

**One conformance measurement is twenty to fifty minutes.** 1668 cases, each a
compile and a `gcc` run over a 14,000-line C file -- and the file grew by
60KB when the Unicode table went in, which lengthened every one of them. So a
measurement is a CHECKPOINT and not a check: it is started, work continues,
and it is compared when it lands. What it measures is the snapshot of `src/`
taken when it STARTED, so a measurement that lands after four more fixes does
not include them, and saying so beside the number matters.

**One corpus run is two minutes** and covers 130 programs on three execution
paths. That is the loop to stay inside. Nearly every divergence this session
found came from it and not from conformance: `isinstance(D(), C)` false on the
C backend alone, the host answering a handle where a raw word was wanted, a
`SimpleNamespace` repr sorted in one path and not the other.

**One case through `conformance/try.py` is fifteen seconds.** That is the
inner loop, and it is the one worth protecting -- which is why the batches
above are three or four cases and not thirty.

**THE IR INTERPRETER NEEDS NO C COMPILER AT ALL**, and it is the loop nobody
was using. Compiling a program and running it through `Interpreter(...)` is a
second or two, it exercises the same frontend, and it can be run BESIDE a
measurement -- which the C backend cannot. Most of what this session found was
found that way: a fifteen-line script through the interpreter, compared with
CPython by eye, and only then promoted to a corpus program.

**And a FRONTEND-ONLY check is a tenth of a second.** `compile_source(...).ok`
answers whether the IR verifier accepted the program, which is the whole
question for a bug class that produces invalid IR --
`tests/asmpython/unit/test_suspension_positions.py` sweeps forty-four shapes
in two seconds because it asks nothing else.

**Do not run two of them at once.** Both fail in ways that are not in the
code: `gcc` reports "out of memory" and the harness records a timeout. Half an
hour went into a run that reported 71 failures and had none. Free memory is
worth checking before a long run, and it is not always this process that is
using it.

## Tools, and what each is for

|  |  |
| --- | --- |
| `conformance/harness.py` | the score. Slow — a compile and link per case. |
| `conformance/harness.py --merge N` | N cases compiled into ONE program. Sees what a case-per-process run cannot -- state the runtime keeps for the length of one program -- and pays one `gcc` per batch instead of one per case. |
| `conformance/try.py` | one case or one snippet, want vs got, with the compiler's own stderr. |
| `tools/objects_diff.py` | the object runtime against CPython, ~137k generated cases. No compiler involved, so a failure is the runtime's and can be nothing else. |
| `tools/dynamic_diff.py` | random dynamic Python, compiled and diffed against CPython. Covers the LOWERING, which `objects_diff` cannot. |
| `tools/crash_scan.py` | which cases make the compiler crash rather than refuse. |
| `tests/.../test_suspension_positions.py` | 44 shapes an `await` or a `yield` can sit in, asked only whether the IR verifier accepts them. Two seconds, no backend, safe to run beside a measurement. |
| `tests/.../test_host_state_is_per_run.py` | the same program run twice, and after a different one, in the INTERPRETER. Catches a table that should be per host and is not. |
| `tests/.../test_bundled_unicodedata.py` | the bundled module against CPython's, over every run boundary and every mapping. |

The last one exists because the harness records a traceback, invalid IR, a link
error and an honest diagnostic all as REFUSED. Only the last is a subset
boundary; the others are bugs sitting inside a bucket labelled "not implemented
yet", and the score does not move when one is fixed.

**No case crashes -- 1679 measured, and every refusal accounted for by name.**
The count was 169 at the rewrite, then zero, then ONE AGAIN: `await` in an
expression position, producing invalid IR. Nobody noticed because the harness
files invalid IR under REFUSED, so the score never moved and this file went on
claiming zero.

HOW THE CURRENT CLAIM WAS CHECKED, which is cheaper than `crash_scan.py` and
says more: the measurement's own log carries each refusal's stderr, so it
answers the question directly. Zero `Traceback`, zero `E9999`, zero `E9104`,
and the twenty-eight non-passing cases divide as

    19  E0091  compile()/eval()/exec() -- the counted gap, all of it
     3  E0083  no `operator`, no `weakref`      (impl tier)
     6  FAIL   identity, interning, the recursion limit, PEP 709 (impl tier)

Three of those printed no detail -- the harness suppresses it for impl-tier
cases -- so they were checked one at a time through `try.py` rather than
assumed. EVERY REFUSAL IN THE SUITE IS A DIAGNOSTIC, which is the invariant,
and it is now a statement about what each case reported rather than about a
count.

The claim is still only as fresh as the last measurement, and it was stale
once already.

AND THE SCAN IS NOT ENOUGH ON ITS OWN, which is the lesson of the twelve
suspension shapes found this session. `crash_scan.py` asks about the CASES,
and the cases put every `await` on a line of its own -- the one position that
worked. A construct the suite does not write is a construct the suite cannot
find, however carefully it is scanned; `test_suspension_positions.py` exists
because the shapes had to be enumerated rather than sampled.

The original 169 had three causes: a `bytes` or
`complex` literal that type-checked and then hit an assertion in lowering (59
of them, now a diagnostic and, for bytes, a supported kind); an attribute
expression the same way; and a `return` inside a `try` with a `finally`, which
lowered the finally body into a block that already ended in `ret`.

That last one was a real semantic bug behind the crash, not just a missing
guard: a `return` has to RUN the enclosing `finally` bodies before returning,
which is what makes `try: return a / finally: return b` answer `b`. Keep the
count at zero -- the invariant is that the compiler never raises on Python it
does not support, and a crash inside the REFUSED bucket is invisible to the
score.

## Invariants worth not breaking

* **Every path agrees**: CPython, the reference interpreter, the C backend,
  x86-64 and bare-metal AArch64, on the same program.
  `tests/asmpython/integration/test_dynamic_python.py` checks three of them
  over a corpus; the differential fuzzers cover the rest.
* **A runtime symbol needs a host binding.** 118 of 177 were unbound at one
  point, so `asmpython run` trapped on programs the compiled binary ran
  correctly. `tests/asmpython/unit/test_objects_host.py` is a ratchet over what
  remains.
* **`conformance/cases/` is never edited.** Bending a case to match the
  compiler is the one change that makes the whole measurement worthless.
* **Anything a compiled program keeps in a FILE STATIC belongs on the host.**
  The C keeps its tables in statics and each compiled program is its own
  process, so one table is one program; `ir/objects_host.py` runs MANY
  programs in one process, and a module-level table hands the second one the
  first's rows. Broken five times so far, and it fails as a WRONG ANSWER on
  the second run -- which reads as a flaky test, because whether the two runs
  share a worker decides who sees it.
  `tests/asmpython/unit/test_host_state_is_per_run.py` runs a program twice,
  and after a different one, and is mutation-checked.
* **A SUSPENSION MAY APPEAR WHEREVER AN EXPRESSION MAY**, and nothing computed
  before it may be held in a register. `await`, `yield`, `yield from` and an
  `async for` inside a comprehension all compile to a return out of the step
  function. `tests/asmpython/unit/test_suspension_positions.py` enumerates
  forty-four positions; adding a lowering that holds a value across an operand
  means adding a case to it.
* `archived/legacy/` is the pre-rewrite compiler. It is not part of 3.14 and is
  not touched.
