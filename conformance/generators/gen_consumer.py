"""Generate the consumer cross-product: every CONSUMER over every ELEMENT KIND.

    python conformance/generators/gen_consumer.py
    python conformance/regen.py --groups generated/consumer

The companion to gen_boundary.py. That one moves a value across boundaries;
this one puts values in a container and varies who reads them back.

Both axes matter and they fail independently. A container's elements can be
stored correctly and still be misread, because a container is consumed by far
more paths than the subscript everyone tests: iteration, enumerate, zip, repr,
min/max, sorted, slicing, unpacking, membership, pop, index. An implementation
routinely gets subscript right and repr wrong, and only a cross-product shows
that as a shape rather than as scattered anecdotes.

    cases/generated/consumer/<consumer>/<element-kind>.py

The `mixed` column is the interesting one: a heterogeneous container is where a
static implementation must actually carry each element's kind at run time
rather than inferring one for the whole container.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cases" / "generated" / "consumer"

#: Element kinds. `mixed` deliberately includes None and a bool, the two values
#: most often conflated with a plain integer by a compiled implementation.
ELEMENTS: dict[str, str] = {
    "int": "[3, 1, 2]",
    "str": "['c', 'a', 'b']",
    "float": "[3.5, 1.5, 2.5]",
    "bool": "[True, False, True]",
    "mixed": "[1, 'two', 3.5, True, None]",
    "nested-list": "[[1, 2], [3], [4, 5, 6]]",
    "tuple-elems": "[(1, 'a'), (2, 'b')]",
    "dict-elems": "[{'a': 1}, {'b': 2}]",
    "bytes-elems": "[b'ab', b'cd']",
    # The empty list is the edge every off-by-one lands on, and its repr,
    # slices and iteration are all still well defined -- but the consumers
    # that index it are not (see INCOMPATIBLE).
    "empty": "[]",
}

#: (consumer, element-kind) pairs that cannot exist. Only `empty` needs any:
#: a consumer that reads xs[0], pops, or takes a min has nothing to read, and
#: CPython raises IndexError or ValueError, so there is no expectation to
#: record. A case the reference implementation cannot run is not a case.
#:
#: Explicit rather than "whatever regen refuses", for the same reason as
#: gen_boundary.INCOMPATIBLE: impossible and broken must not look alike.
_NEEDS_AN_ELEMENT = {
    "subscript", "subscript-computed", "negative-index", "unpack-first",
    "membership", "index-method", "pop-method", "remove-method",
    "min-max-by-repr", "pass-to-function", "return-from-function",
    "store-in-instance", "dict-value", "star-args",
}
INCOMPATIBLE: dict[str, set[str]] = {
    consumer: {"empty"} for consumer in _NEEDS_AN_ELEMENT
}

#: Each consumer reads `xs` and prints something derived from its ELEMENTS.
#: Sort-based consumers are given a key so a heterogeneous list does not raise
#: TypeError under CPython -- the point is the read path, not comparison rules,
#: and a case that raises under the reference implementation cannot be recorded.
CONSUMERS: dict[str, str] = {
    "subscript": "print(xs[0])\nprint(xs[len(xs) - 1])\n",
    "subscript-computed": "i = len(xs) // 2\nprint(xs[i])\n",
    "negative-index": "print(xs[-1])\n",
    "for-loop": "for v in xs:\n    print(v)\n",
    "enumerate": "for i, v in enumerate(xs):\n    print(i, v)\n",
    "zip": "for a, b in zip(xs, xs):\n    print(a, b)\n",
    "len": "print(len(xs))\n",
    "repr-container": "print(xs)\n",
    "repr-nested": "print([xs])\n",
    "slice": "print(xs[1:])\nprint(xs[:1])\n",
    "slice-step": "print(xs[::2])\nprint(xs[::-1])\n",
    "unpack-first": "head = xs[0]\nrest = xs[1:]\nprint(head)\nprint(rest)\n",
    "membership": "print(xs[0] in xs)\nprint(xs[-1] in xs)\n",
    "index-method": "print(xs.index(xs[0]))\nprint(xs.index(xs[-1]))\n",
    "pop-method": "ys = list(xs)\nprint(ys.pop())\nprint(len(ys))\n",
    "remove-method": "ys = list(xs)\nys.remove(xs[0])\nprint(len(ys))\nprint(ys)\n",
    "copy-via-list": "ys = list(xs)\nprint(ys)\nprint(ys == xs)\n",
    "concat": "print(xs + xs[:1])\n",
    "comprehension": "print([v for v in xs])\n",
    "comprehension-nested": "print([[v] for v in xs])\n",
    "sorted-by-repr": "print(sorted(xs, key=repr))\n",
    "min-max-by-repr": "print(min(xs, key=repr))\nprint(max(xs, key=repr))\n",
    "reversed": "print(list(reversed(xs)))\n",
    "pass-to-function": (
        "def take(seq):\n    return seq[0]\n\nprint(take(xs))\n"
    ),
    "return-from-function": (
        "def give():\n    return xs\n\nprint(give()[0])\nprint(give())\n"
    ),
    "store-in-instance": (
        "class _Holder:\n    def __init__(self, seq):\n        self.seq = seq\n\n"
        "h = _Holder(xs)\nprint(h.seq[0])\nprint(h.seq)\n"
    ),
    "dict-value": "d = {'k': xs}\nprint(d['k'][0])\nprint(d['k'])\n",
    "star-args": (
        "def take(*a):\n    return a[0]\n\nprint(take(*xs))\n"
    ),
}

TEMPLATE = """# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
xs = {elements}
{body}"""


def main() -> int:
    written = skipped = 0
    for consumer, body in CONSUMERS.items():
        excluded = INCOMPATIBLE.get(consumer, set())
        for kind, literal in ELEMENTS.items():
            path = OUT / consumer / f"{kind}.py"
            if kind in excluded:
                # Unlink, so an exclusion added later cannot strand a case that
                # is still scored and no longer generated.
                path.unlink(missing_ok=True)
                skipped += 1
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                TEMPLATE.format(elements=literal, body=body), encoding="utf-8"
            )
            written += 1
    print(f"gen_consumer: {written} cases "
          f"({len(CONSUMERS)} consumers x {len(ELEMENTS)} kinds, "
          f"{skipped} impossible pair(s) excluded) -> {OUT}")
    print("now run: python conformance/regen.py --groups generated/consumer")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
