# conformance — asmpython's CPython oracle

**A developer tool, not a distributable package.** It exists so asmpython can be
measured against *the language* instead of against `tests/`, a corpus that grew
up alongside the compiler and therefore cannot contain the shapes the compiler
was never asked to handle.

That distinction is the whole point, and it is not theoretical. Three
parameter-slot defects found in one session were invisible to `tests/` by
construction:

```python
def ident(v):
    return v
print(ident(3.5))     # 3731456     — needs TWO disagreeing call sites
print(ident("s"))     # 5368766490  — a raw pointer
print(ident(True))    # 1           — bool-ness dies at the first call
```

A corpus written by the same people who write the compiler tests what the
compiler already does. An oracle derived from CPython tests what Python *is*.

**1656 cases. 121 PEPs. CPython passes 100% of the counted tiers.**

| area | what it covers |
|---|---|
| `generated/` | four cross-products — value × storage trip, container × reader, operand pair × operator, container × constructor |
| `datamodel/` | every special method: attribute access, containers, numerics, comparison, metaclasses, descriptors |
| `quirks/` | the famous flaws — `t[0] += [x]` raising *and* mutating, `nan != nan`, `{1: …, True: …}` collapsing |
| `pep/` | 95 PEPs, from list comprehensions (202) to t-strings (750) |
| `match/` | every pattern kind: literal, capture, sequence, mapping, class, as, value, guards |
| `evaluation-order/` | what happened *first* — probes append to a log and the log is asserted |
| `aliasing/` | two names, one object |
| `scoping/` `exceptions/` `iteration/` `async/` | the execution model, exception hierarchy, generator protocol, coroutines |
| `numeric/` `text/` `sequence/` `mapping/` `sets/` | built-in types by behaviour *and* by API surface |

The three areas the cross-products structurally cannot express — **order**,
**aliasing** and **cleanup** — are hand-written, because a reordering that
happens to produce the right answer is invisible to a value-comparison test.

---

## The problem this format solves

Running CPython tells you what CPython **does**, not what the language
**requires**. Some observable CPython behaviour is an implementation accident:

```python
a = 256; b = 256; a is b      # True
a = 257; b = 257; a is b      # False  <- small-int caching, not a guarantee
id(x)                          # an address, not a specified value
hash("abc")                    # salted per process
```

Pinning those would hold asmpython to CPython's *accidents* — refcount-driven
collection timing, small-int caching, address values — none of which asmpython
should reproduce. Pinning *nothing* misses the microbehaviour the oracle exists
for.

So every case declares which side of that line it sits on, and the tier is a
field, not a convention. The `impl` tier is what lets the oracle record a
divergence without asserting asmpython is wrong to have it.

| tier | meaning | counted in the score |
|---|---|---|
| `spec` | Required by the Language or Library Reference. **Must** cite a section. | yes |
| `cpython` | Observable CPython behaviour the spec does not mandate but real code depends on. | yes |
| `impl` | Implementation accident: `id()` values, interning, `hash()` values, `__del__` timing, recursion limits. | **no** |

`impl` cases are still recorded and still run — documenting *how* an
implementation diverges is useful — but they never count against it.

**Contested behaviour defaults to `impl`** until someone produces a citation.
The suite deliberately under-claims: a false failure costs more credibility
than a missed divergence costs coverage.

---

## Case format

A case is one file. Its **path is its identity** — `cases/numeric/int/overflow-add-boundary.py`
has the stable id `numeric/int/overflow-add-boundary`. Cases are not renumbered
and not moved; that is what makes a result comparable across months of work on
an implementation rather than only across one commit.

```python
# tier: spec
# ref: reference/expressions.html#binary-arithmetic-operations
# expect:
# 9223372036854775808
print(9223372036854775807 + 1)
```

Rules the harness enforces:

- Header fields come **before** `# expect:`. Everything after that marker is
  expected stdout, so a trailing field would silently become an expected line.
- `tier: spec` requires a `ref:`.
- `min-python: 3.12` marks a case whose feature does not exist further back.
  The harness and `regen.py` both skip it on an older interpreter and announce
  the count. Not having a feature yet is not a divergence, and without the field
  a `SyntaxError` would be recorded as the expected output and then enforced.
- Expected output is **derived, never hand-written** — `regen.py` runs the case
  under CPython and writes the block. A hand-typed expectation is how a suite
  ends up asserting something the reference implementation does not actually do.
- A case must be deterministic. `regen.py` runs it twice in separate processes
  and refuses to record disagreement, which catches `PYTHONHASHSEED` (set and
  dict ordering of strings differs per process), clock reads, and `id()` leaking
  into output.

---

## The self-test, and why it matters

**CPython must score 100% on `spec` + `cpython`.** If it does not, the bug is
in the suite: a wrong expectation, a nondeterministic case, or a behaviour
mis-tiered as normative when it is an accident.

```
python conformance/selftest.py
```

This runs continuously and for free, and it catches the failure mode
hand-authored suites cannot: a case that asserts something no implementation
should be expected to satisfy.

---

## Scoring asmpython

```
python conformance/harness.py --shim asmpython --matrix
python conformance/harness.py --shim cpython
python conformance/harness.py --shim asmpython --tier spec,cpython
python conformance/harness.py --shim asmpython --matrix          # cross-product view
python conformance/harness.py --list-groups                      # what can be selected
python conformance/harness.py --shim asmpython --groups pep,functions
python conformance/harness.py --shim asmpython --groups generated/boundary
```

Every run of the whole suite is a compile-and-link per case, so `--groups`
exists for the quick check while you iterate; run it bare before you conclude
anything.

`-j N` sets the worker count and `--exec process` runs cases in separate
processes instead of threads. On a 4-core machine that measured no faster —
compiling saturates the cores and `subprocess.run` releases the GIL anyway — so
reach for it only if you have measured a win on your own hardware, **running
the comparison both ways round**. Ordering matters more than the executor here:
the first run of any pair pays for a cold runtime build and a cold file cache,
which is enough to invent a 1.8× speedup that does not exist.

A group is any directory prefix of a case id, so you choose the granularity. An
unknown group is a hard error rather than an empty selection — zero cases score
100%, which is the most dangerous way to be wrong.

`selftest.py` takes the same groups positionally: `python selftest.py pep`.

There are two shims and they play different roles. `cpython` is the **oracle** —
it is what `regen.py` derives expectations from and what `selftest.py` checks
the suite against. `asmpython` is the **subject**. Everything else in the tree
is implementation-neutral, which is not an aspiration toward portability but the
property that keeps the oracle honest: a suite that knew about asmpython could
be bent, however unintentionally, toward what asmpython already does.

A shim is small — given a case file, produce its stdout:

```python
def run(case_path, timeout):
    """-> (stdout, stderr, returncode). returncode None means REFUSED."""
```

`shims/asmpython.py` passes `--no-pyinbin-fallback`, and that flag is
load-bearing: without it the CLI silently interprets a source the native backend
rejected and exits 0, so a compile refusal is indistinguishable from a pass.
Scoring an interpreted fallback as native conformance would make the whole
measurement meaningless.

---

## Triaging a result

Most of `cases/` is four cross-products, each varying one thing:

| tree | axes | asks |
|---|---|---|
| `generated/boundary/<trip>/<kind>` | 20 × 20 | does a value survive being **moved**? |
| `generated/consumer/<consumer>/<kind>` | 28 × 10 | does a container survive being **read**? |
| `generated/operator/<op>/<left>-<right>` | 16 × 19 | does a pair survive being **operated on**? |
| `generated/conversion/<ctor>/<source>` | 12 × 12 | does a container survive being **converted**? |

The axis names are the path, so a failing case names its own coordinates, and
hundreds of failures collapse into a handful of causes.

`--matrix` prints that decomposition: which whole **columns** fail (a broken
value kind, independent of any boundary) versus which whole **rows** fail (a
boundary that loses representation whatever crosses it). Those are opposite work
queues and a case-by-case list cannot distinguish them.

[TAXONOMY.md](TAXONOMY.md) names the recurring causes —
`representation-follows-slot`, `monomorphic-inference`, `kind-conflation`,
`width-truncation`, `container-depth`, `consumer-gap`, `refused`,
`formatter-only` — with the diagnostic that distinguishes each from the others.

---

## What this suite will not tell you

Stated plainly, because a conformance suite invites over-reading:

- **Performance and memory.** Nothing here is a benchmark.
- **Concurrency and GIL semantics.** Timing-dependent behaviour is untestable
  by exact-output comparison.
- **C-extension ABI compatibility.**
- **Anything requiring multiple modules, the filesystem, sockets or
  subprocesses**, until fixtures exist for them.
- **Legitimate divergence.** An implementation may deliberately differ; the
  `impl` tier records that without judging it.

A high score means "matches CPython on the behaviours tested here", which is
narrower than "is a correct Python".
