# A vocabulary for divergences

A conformance suite that only says *which* cases fail makes every implementer
re-derive the same handful of causes from scratch. This file names the causes.

The names exist so a maintainer can write "boundary/tuple-roundtrip/* is
**representation-follows-slot**" in one line instead of re-explaining the
mechanism in every issue. They are not severity levels and not a scoring
system — the harness counts cases, not categories.

---

## Reading a failure before naming it

The generated trees are cross-products, so a failure carries its own
coordinates in its path:

```
generated/boundary/<trip>/<kind>       a value KIND through a storage TRIP
generated/consumer/<consumer>/<kind>   a container read by a CONSUMER
```

Ask which axis moves before opening any source:

- **A whole column fails** (one kind, every trip) — the *value kind* is
  unsupported or misrepresented. Nothing to do with boundaries.
- **A whole row fails** (one trip, every kind) — the *boundary* loses
  representation regardless of what travels across it.
- **A single cell fails** — genuinely the interaction, and the rarest case.

`harness.py --matrix` prints exactly this decomposition. Run it before triage.
On the run that motivated this file, 90 of 139 boundary failures were six
columns — six broken value kinds — and only ~40 were boundary-induced. Those
are opposite work queues, and the cell-by-cell view cannot tell them apart.

---

## The causes

### representation-follows-slot

The dominant cause in a compiled implementation, and the one worth
understanding first.

A value's runtime representation is chosen from the **declared type of wherever
it is stored**, not from the value. Store into a slot typed more weakly than the
value and the representation silently changes; read it back through the weak
type and you get a different value, not an error.

The symptom depends only on how the result is *used*, which is why one root
cause looks like a dozen unrelated bugs: a raw pointer printed as an integer, a
`0`, an access violation, a compile refusal, a `1e-317` denormal.

```python
def ident(v):
    return v
print(ident(3.5))    # 3731456       — the float's bits read as an int
print(ident("s"))    # 5368766490    — the string's address
```

Diagnostic: the value is correct at the write and wrong at the read, and
changing the *declared* type of the intermediate slot changes the answer.

### monomorphic-inference

A special case of the above, called out separately because it is invisible
until a function is used at two types. An implementation infers an unannotated
parameter from its call sites; with one site it infers correctly, with two
conflicting sites it must fall back to a *dynamic* representation — and falling
back to a concrete default instead is a positive wrong claim.

Diagnostic: the case passes when you delete one of the two call sites. A test
corpus grown alongside the implementation will barely contain this shape, which
is exactly why a generated suite finds it.

### kind-conflation

Two Python types share one machine representation and the implementation cannot
tell them apart afterwards. `bool` as `int` is the standard one: `True` prints
as `1`, `type(True).__name__` is `int`. `None` as `0` is the other, and it is
worse — a genuine integer `0` in a field is bit-identical to `None`, so any fix
that guesses from the bits breaks the other direction.

Diagnostic: the *value* is arithmetically right and the *kind* is wrong.
`print(type(x).__name__)` fails while `print(x == expected)` passes. This is why
every generated case prints the type name.

### width-truncation

A value that does not fit the implementation's native word. `int-big`
(`9223372036854775808`) is the whole column. Python integers are arbitrary
precision; a 64-bit implementation must either promote or refuse.

Diagnostic: correct below the boundary, wrong or wrapped above it.

The cross-boundary property is the one to protect: once big integers exist,
a machine-word `5` and a promoted `5` must be equal, hash alike, and print
alike, or you have traded one bug for a subtler one.

### container-depth

Nesting beyond the depth the implementation actually handles. `list-nested`
(`[[1], [2]]`) failing while `list` passes is this, not a list bug. Element
representation is usually decided once, for one level.

### consumer-gap

The container is stored correctly and one particular *reader* is wrong. A
container is consumed by far more paths than the subscript everyone tests:
iteration, `enumerate`, `zip`, `repr`, `min`/`max`, `sorted`, slicing,
unpacking, membership, `pop`, `index`. Getting subscript right and `repr` wrong
is routine.

Diagnostic: same container, one consumer disagrees. A single failing cell in an
otherwise-passing consumer column.

### refused

The implementation rejected the source (`REFUSED`, marked `C`, not `X`). Kept
distinct from a wrong answer throughout the harness because "cannot run this"
and "runs it wrongly" are different bugs with different fixes — and because an
implementation that refuses is at least *honest*, which a wrong answer is not.

Triage refusals separately. They are usually a missing feature; the `X`s are
usually a broken model.

### formatter-only

The value is right and only its rendering is wrong: float shortest-repr
(`0.1 + 0.2`), container `repr` spacing, `-0.0`, exponent thresholds. Cheap to
fix and easy to mistake for a value bug, because the only evidence either way
is printed text.

Diagnostic: `print(x == expected)` says `True` while `print(x)` differs.

---

## Choosing between them

Work outward from the value:

1. Is the **value** wrong, or only its **rendering**? → `formatter-only`.
2. Is the value right but its **type name** wrong? → `kind-conflation`.
3. Does it depend on **magnitude**? → `width-truncation`.
4. Does it depend on **nesting depth**? → `container-depth`.
5. Does it survive one **storage trip** and not another? →
   `representation-follows-slot`; if the trip is a call and a second call site
   is what breaks it, `monomorphic-inference`.
6. Does it depend only on **who reads it**? → `consumer-gap`.

If two apply, prefer the one that names the *mechanism* over the one that names
the symptom — `representation-follows-slot` over `kind-conflation` when a bool
is lost specifically by crossing a boundary, because that is what a fix has to
change.
