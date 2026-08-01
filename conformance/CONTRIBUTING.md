# Contributing a case

The whole suite rests on one property: **CPython scores 100% on the counted
tiers.** Every rule below exists to keep that true, because the moment it isn't,
the suite is asserting something no implementation should have to satisfy and
nobody can tell which cases are trustworthy.

```
python conformance/selftest.py      # must pass before you push
```

---

## Adding a case by hand

1. Put the file where its name should be. **The path is the id.**
   `cases/numeric/int/floor-division-sign.py` is `numeric/int/floor-division-sign`.
   Pick the name for a reader who hasn't seen the code.

2. Write the header and the program. Leave out `# expect:` — it is derived.

   ```python
   # tier: spec
   # ref: reference/expressions.html#binary-arithmetic-operations
   print(-7 // 2)
   ```

3. Derive the expectation:

   ```
   python conformance/regen.py --filter numeric/int/floor-division-sign
   ```

4. Run the self-test.

### Features newer than the oldest supported CPython

Add `# min-python:` when the case uses syntax or a library that does not exist
all the way back:

```python
# tier: spec
# ref: reference/compound_stmts.html#type-params
# min-python: 3.12
def first[T](xs: list[T]) -> T:
    return xs[0]
```

Both the harness and `regen.py` skip such a case on an older interpreter, and
say so rather than skipping silently. Without the field, a 3.12 case run on 3.11
is a `SyntaxError` recorded as the expected output — an expectation that then
gets enforced forever.

The gate keys on the CPython running the **suite**, because that is what derives
and re-validates the expectation. Not having a feature yet is not a divergence.

Never type an `# expect:` block yourself. A hand-written expectation is how a
suite ends up asserting something the reference implementation doesn't actually
do, and that failure is invisible — it reports as an implementation bug
forever.

---

## Choosing a tier

This is the judgement that decides whether anyone trusts the suite.

**`spec`** — the language requires it. Needs a `ref:`. The harness rejects a
`spec` case without one, and that is deliberate: if you cannot find a citation,
you do not yet know that the behaviour is required.

**`cpython`** — CPython does it, the spec doesn't mandate it, real code depends
on it. Exception *message text* is the standard example: the type raised is
specified, the wording is not, but code and tests read the wording.

**`impl`** — an implementation accident. Run and recorded, never counted.

```python
a = 256; b = 256; a is b     # True  — small-int caching
a = 257; b = 257; a is b     # False — same code, different answer
```

Nothing requires that. Counting it would fail PyPy, MicroPython and GraalPy for
something that is not their fault.

**When unsure, choose the weaker tier.** A false failure costs more credibility
than a missed divergence costs coverage. Contested behaviour sits in `impl`
until somebody produces a citation, and moving a case *up* a tier later is
cheap — moving it down after an implementer has dismissed the suite is not.

### Things that are never `spec`

`id()` values · object identity for equal immutables · `hash()` values ·
`__del__` timing · `sys.getsizeof` · recursion limits · exact traceback text ·
GC behaviour · dict/set order beyond documented insertion order · anything
timing-dependent.

---

## Determinism

`regen.py` runs each case **twice, in separate processes**, and refuses to
record disagreement. Things that trip it:

- **Sets and dicts of strings.** `PYTHONHASHSEED` is per process, so iteration
  order is a different correct answer each run. Sort before printing.
- **`id()` or anything derived from an address.**
- **Clocks**, including `time`, `perf_counter`, and anything scheduling-related.
- **Iteration over an unordered collection**, even indirectly.

If regen refuses your case, the case is wrong, not regen.

### Keep printed output ASCII

`sys.stdout`'s encoding is environmental. A case that prints U+FFFD raises
`UnicodeEncodeError` in **CPython itself** on a Windows console codepage, so
there is no expectation to record and the case is unrunnable for half your
audience. Print `ascii(x)`, `ord(x)`, or a length instead — the value is what
the case is about, its console representation is not.

---

## Writing a good case

- **One behaviour per case.** A case testing three things tells you almost
  nothing when it fails.
- **Print, don't assert.** The suite compares stdout. `assert` turns a
  informative diff into a traceback.
- **Print the kind as well as the value** where it matters
  (`print(type(x).__name__)`). Value and kind fail independently, and an
  implementation can get one right and the other wrong.
- **Self-contained.** No imports outside the standard library, no files, no
  network, no subprocesses.
- **Small.** If it needs more than about twenty lines, it is probably several
  cases.

---

## Generated cases

`generators/` builds four cross-products — `boundary/` (a value across storage
boundaries), `consumer/` (a container read by every consumer), `operator/`
(a pair of values under every operator) and `conversion/` (a container through
every constructor). Edit the generator, never the generated files:

```text
python conformance/generators/gen_boundary.py
python conformance/regen.py --groups generated/boundary
```

The axis names become the path, so a failing cell names its own coordinates.
Keep it that way: it is what lets hundreds of failures collapse into a handful
of causes.

Two rules learned the hard way, both about making a product *mean* something:

- **An impossible pair is excluded explicitly**, not by letting regen fail on
  it. A dict key must be hashable, so `dict-key-roundtrip × list` cannot exist;
  `gen_boundary.INCOMPATIBLE` says so and unlinks any stale file. If impossible
  and broken look the same, a real defect in the oracle hides among them.
- **Pick literals that exercise the interesting path.** The operator product's
  containers contain its scalars, so `contains` hits the found path. A
  membership test that only ever misses never runs the comparison the scan
  performs on each element — sixteen cells that all pass and assert nothing.

---

## Adding an implementation

Drop a file in `shims/`:

```python
def run(case_path, timeout):
    """-> (stdout, stderr, returncode).

    returncode None means the implementation REFUSED to run the program
    (a compiler rejecting the source). The harness reports that as REFUSED
    rather than FAIL, because "cannot run this" and "runs it wrongly" are
    different bugs.
    """
```

If your implementation has an interpreter fallback, **disable it**. A suite
that scores an interpreted fallback as native conformance measures nothing.
