"""Generate container-element CONSUMER probes, as an explicit coverage matrix.

`_list_elem_addr` returns an element ADDRESS, so each of its 64 call sites
across 27 functions performs its own load and makes its own typing decision.
That is why the element representation policy is smeared across the lowering
rather than decided in one place, and it is why changing the representation has
twice cost more than it bought: boxing bare-list locals measured -10/+5, boxing
heterogeneous tuple slots measured -26/+1. Both times the write side and one
read site were changed together, and the other consumers disagreed.

A refactor that routes those 64 sites through `_lower_load_element` /
`_lower_store_element` is only verifiable if every consumer path is observable
from a test. This file makes them observable. It is written as a CROSS PRODUCT
rather than as a list of hand-picked cases, because the property that matters
is completeness: a consumer with no probe is exactly the one that will
silently disagree after the migration, and a hand-written set cannot show that
it has no holes.

    paths   x   element kinds
    ------      -------------
    26          int / str / float / mixed

Each emitted case is `elem_<path>_<kind>.py`, so the matrix can be rebuilt
mechanically from the corpus filenames and the runner's verdicts -- the report
is derived, not maintained.

`mixed` is the kind that actually breaks: `[1, "two", 3.5, True, None]` forces
every consumer to carry a per-element kind rather than a per-container one. For
the ordering paths (`min`/`max`/`sorted`/`sort` without a key) CPython raises
TypeError on mixed input, so those probes assert the refusal -- that is the
correct behaviour, and a compiler that silently returns a value instead is
wrong in the direction this corpus most wants to catch.

Nested containers (list-of-tuples, dict-of-lists) get a dedicated section
rather than a full cross product: the interesting property there is that the
INNER container's elements survive being read out of the outer one.

Usage: python gen_elem_cases.py <tests/cases dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _emit import CaseSet, main  # noqa: E402

CASES = CaseSet("probes")
case = CASES.case


# ---------------------------------------------------------------------------
# element kinds
# ---------------------------------------------------------------------------
#
# `seq` is the canonical container. `triple` is a 3-element form for paths that
# need a fixed arity (starred calls, set operations). `mixed` deliberately
# carries an int, a str, a float, a bool and None, because bool-vs-int and
# None-vs-0 are the two distinctions a 64-bit-word representation loses first.
#
# `triple`/`triple2` for `mixed` exclude True: {1, True} collapses to a single
# set member in CPython (they are equal and hash equal), which would make the
# set-operation probes test that subtlety instead of the element kind.

KINDS: dict[str, dict] = {
    "int": dict(
        seq="[10, 20, 30, 40]", triple="[10, 20, 30]", triple2="[20, 30, 50]",
        pair="(10, 20)", pairs="[(10, 20), (30, 40)]",
        present="20", absent="99", orderable=True,
    ),
    "str": dict(
        seq='["aa", "bb", "cc", "dd"]', triple='["aa", "bb", "cc"]',
        triple2='["bb", "cc", "ee"]',
        pair='("aa", "bb")', pairs='[("aa", "bb"), ("cc", "dd")]',
        present='"bb"', absent='"zz"', orderable=True,
    ),
    "float": dict(
        seq="[1.5, 2.5, 3.5, 4.5]", triple="[1.5, 2.5, 3.5]",
        triple2="[2.5, 3.5, 5.5]",
        pair="(1.5, 2.5)", pairs="[(1.5, 2.5), (3.5, 4.5)]",
        present="2.5", absent="9.5", orderable=True,
    ),
    "mixed": dict(
        seq='[1, "two", 3.5, True, None]', triple='[1, "two", 3.5]',
        triple2='["two", 3.5, 9]',
        pair='(1, "two")', pairs='[(1, "two"), ("three", 4.5)]',
        present='"two"', absent='"zz"', orderable=False,
    ),
}


# ---------------------------------------------------------------------------
# consumer paths -- one builder per path, returning the case source
# ---------------------------------------------------------------------------

def p_subscript(k):
    return f'''
xs = {k["seq"]}
print(xs[0])
print(xs[1])
print(xs[-1])
'''


def p_subscript_computed(k):
    return f'''
xs = {k["seq"]}
i = 1
print(xs[i])
print(xs[i + 1])
print(xs[len(xs) - 1])
'''


def p_for_loop(k):
    return f'''
xs = {k["seq"]}
for v in xs:
    print(v)
'''


def p_enumerate(k):
    return f'''
xs = {k["seq"]}
for i, v in enumerate(xs):
    print(i, v)
'''


def p_zip(k):
    return f'''
xs = {k["seq"]}
ys = {k["seq"]}
for a, b in zip(xs, ys):
    print(a, b)
'''


def p_unpack(k):
    return f'''
pair = {k["pair"]}
a, b = pair
print(a)
print(b)
'''


def p_unpack_in_for(k):
    return f'''
pairs = {k["pairs"]}
for left, right in pairs:
    print(left, right)
'''


def p_repr_container(k):
    return f'''
xs = {k["seq"]}
print(xs)
print(repr(xs))
print(str(xs))
'''


def p_repr_nested_in_container(k):
    return f'''
xs = {k["seq"]}
print([xs])
print({{"k": xs}})
print((xs,))
'''


def p_len(k):
    return f'''
xs = {k["seq"]}
print(len(xs))
print(len(xs) > 0)
'''


def p_membership(k):
    return f'''
xs = {k["seq"]}
print({k["present"]} in xs)
print({k["absent"]} in xs)
print({k["absent"]} not in xs)
'''


def p_index_method(k):
    return f'''
xs = list({k["seq"]})
print(xs.index({k["present"]}))
'''


def p_remove_method(k):
    return f'''
xs = list({k["seq"]})
xs.remove({k["present"]})
print(xs)
print(len(xs))
'''


def p_pop_method(k):
    return f'''
xs = list({k["seq"]})
print(xs.pop())
print(xs.pop(0))
print(xs)
'''


def p_slice(k):
    return f'''
xs = {k["seq"]}
print(xs[1:3])
print(xs[:2])
print(xs[-2:])
'''


def p_slice_step(k):
    return f'''
xs = {k["seq"]}
print(xs[::2])
print(xs[::-1])
print(xs[1::2])
'''


def p_starred_call(k):
    return f'''
def take3(a, b, c):
    return [a, b, c]


xs = {k["triple"]}
print(take3(*xs))
'''


def p_dict_from_pairs(k):
    return f'''
pairs = {k["pairs"]}
built = dict(pairs)
print(built)
print(len(built))
'''


def p_comprehension_list(k):
    return f'''
xs = {k["seq"]}
print([v for v in xs])
'''


def p_comprehension_dict(k):
    return f'''
xs = {k["seq"]}
print({{i: v for i, v in enumerate(xs)}})
'''


def p_comprehension_set(k):
    return f'''
xs = {k["seq"]}
print(sorted({{str(v) for v in xs}}))
'''


def p_comprehension_nested(k):
    return f'''
rows = [{k["triple"]}, {k["triple"]}]
print([[v for v in row] for row in rows])
'''


def p_set_ops(k):
    return f'''
a = set({k["triple"]})
b = set({k["triple2"]})
print(sorted(a | b, key=str))
print(sorted(a & b, key=str))
print(sorted(a - b, key=str))
'''


def p_pass_to_function(k):
    return f'''
def count(xs):
    total = 0
    for _ in xs:
        total = total + 1
    return total


def first_of(xs):
    return xs[0]


xs = {k["seq"]}
print(count(xs))
print(first_of(xs))
'''


def p_min_max(k):
    if k["orderable"]:
        return f'''
xs = {k["seq"]}
print(min(xs))
print(max(xs))
'''
    # CPython refuses to order a heterogeneous sequence. Asserting the refusal
    # is the point: silently returning a value here is the failure this corpus
    # most wants to catch.
    return f'''
xs = {k["seq"]}
try:
    print(min(xs))
    print("min returned a value")
except TypeError:
    print("min refused")
'''


def p_min_max_key(k):
    return f'''
xs = {k["seq"]}
print(min(xs, key=str))
print(max(xs, key=str))
'''


def p_sorted(k):
    if k["orderable"]:
        return f'''
xs = {k["seq"]}
print(sorted(xs))
print(sorted(xs, reverse=True))
'''
    return f'''
xs = {k["seq"]}
try:
    print(sorted(xs))
    print("sorted returned a value")
except TypeError:
    print("sorted refused")
'''


def p_sorted_key(k):
    return f'''
xs = {k["seq"]}
print(sorted(xs, key=str))
'''


def p_sort_inplace(k):
    if k["orderable"]:
        return f'''
xs = list({k["seq"]})
xs.sort()
print(xs)
'''
    return f'''
xs = list({k["seq"]})
try:
    xs.sort()
    print("sort returned")
except TypeError:
    print("sort refused")
'''


def p_sort_inplace_key(k):
    return f'''
xs = list({k["seq"]})
xs.sort(key=str)
print(xs)
'''


PATHS: list[tuple[str, str, object]] = [
    ("subscript", "a container element is read by literal index", p_subscript),
    ("subscript_computed", "a container element is read by computed index", p_subscript_computed),
    ("for_loop", "for-loop iteration reads each element", p_for_loop),
    ("enumerate", "enumerate() reads each element", p_enumerate),
    ("zip", "zip() reads elements from two containers", p_zip),
    ("unpack", "tuple unpacking reads both elements", p_unpack),
    ("unpack_in_for", "a for target destructures each element", p_unpack_in_for),
    ("repr_container", "the container renders its own elements", p_repr_container),
    ("repr_nested", "a container renders inside another container", p_repr_nested_in_container),
    ("len", "len() over the container", p_len),
    ("membership", "`in` compares against each element", p_membership),
    ("index_method", "list.index finds an element by value", p_index_method),
    ("remove_method", "list.remove deletes an element by value", p_remove_method),
    ("pop_method", "list.pop returns the removed element", p_pop_method),
    ("slice", "a slice copies a run of elements", p_slice),
    ("slice_step", "an extended slice copies with a step", p_slice_step),
    ("starred_call", "f(*xs) spreads elements into parameters", p_starred_call),
    ("dict_from_pairs", "dict(pairs) reads both halves of each pair", p_dict_from_pairs),
    ("comprehension_list", "a list comprehension reads each element", p_comprehension_list),
    ("comprehension_dict", "a dict comprehension reads each element", p_comprehension_dict),
    ("comprehension_set", "a set comprehension reads each element", p_comprehension_set),
    ("comprehension_nested", "a nested comprehension reads inner elements", p_comprehension_nested),
    ("set_ops", "set operations compare elements", p_set_ops),
    ("pass_to_function", "a container passed to a function is read there", p_pass_to_function),
    ("min_max", "min/max order the elements", p_min_max),
    ("min_max_key", "min/max with key= read each element", p_min_max_key),
    ("sorted", "sorted() orders the elements", p_sorted),
    ("sorted_key", "sorted(key=) reads each element", p_sorted_key),
    ("sort_inplace", "list.sort orders in place", p_sort_inplace),
    ("sort_inplace_key", "list.sort(key=) reads each element", p_sort_inplace_key),
]

for _kind, _spec in KINDS.items():
    for _pname, _desc, _builder in PATHS:
        case(f"elem_{_pname}_{_kind}", f"{_desc} ({_kind} elements)", _builder(_spec))


# ---------------------------------------------------------------------------
# nested containers -- the inner container's elements must survive being read
# out of the outer one
# ---------------------------------------------------------------------------

case("elem_nested_list_of_tuples_subscript",
     "an element of a tuple inside a list survives the outer read", r'''
rows = [(1, "a"), (2, "b")]
print(rows[0])
print(rows[0][0])
print(rows[0][1])
print(rows[1][1])
''')

case("elem_nested_list_of_tuples_iterate",
     "iterating a list of tuples yields intact tuples", r'''
rows = [(1, "a"), (2, "b")]
for row in rows:
    print(row)
    print(row[1])
''')

case("elem_nested_list_of_tuples_unpack",
     "a list of tuples destructures in the for target", r'''
rows = [(1, "a"), (2, "b")]
for number, label in rows:
    print(number, label)
''')

case("elem_nested_list_of_tuples_sorted",
     "sorting a list of tuples orders by the first element", r'''
rows = [(2, "b"), (1, "c"), (1, "a")]
print(sorted(rows))
''')

case("elem_nested_list_of_tuples_dict",
     "a list of pair tuples converts to a dict", r'''
rows = [(1, "a"), (2, "b")]
built = dict(rows)
print(built)
print(built[1])
''')

case("elem_nested_list_of_lists_mutate",
     "mutating an inner list through the outer container", r'''
grid = [[1, 2], [3, 4]]
grid[0].append(9)
print(grid)
print(len(grid[0]))
''')

case("elem_nested_dict_of_lists_read",
     "a list stored as a dict value keeps its elements", r'''
groups = {"x": [1, 2], "y": ["a"]}
print(groups["x"])
print(groups["x"][1])
print(groups["y"][0])
''')

case("elem_nested_dict_of_lists_iterate",
     "iterating a dict of lists yields intact lists", r'''
groups = {"x": [1, 2], "y": [3]}
for key in groups:
    print(key, groups[key])
''')

case("elem_nested_dict_of_lists_append",
     "appending to a list held as a dict value", r'''
groups = {"x": [1]}
groups["x"].append(2)
print(groups["x"])
print(len(groups["x"]))
''')

case("elem_nested_dict_of_dicts",
     "a dict inside a dict keeps its own entries", r'''
config = {"outer": {"inner": 1, "other": "two"}}
print(config["outer"])
print(config["outer"]["inner"])
print(config["outer"]["other"])
''')

case("elem_nested_mixed_depth_three",
     "a three-level nesting of mixed kinds survives", r'''
tree = {"rows": [(1, ["a", 2.5]), (2, ["b", None])]}
print(tree["rows"][0])
print(tree["rows"][0][1][0])
print(tree["rows"][0][1][1])
print(tree["rows"][1][1][1])
''')

case("elem_nested_tuple_of_lists",
     "a list inside a tuple stays mutable and intact", r'''
holder = ([1, 2], ["a"])
holder[0].append(3)
print(holder)
print(holder[0])
print(holder[1][0])
''')

case("elem_nested_comprehension_over_pairs",
     "a comprehension over a list of tuples reads both halves", r'''
rows = [(1, "a"), (2, "b")]
print([label for _, label in rows])
print({number: label for number, label in rows})
''')

case("elem_nested_list_of_tuples_index",
     "list.index locates a tuple element by value", r'''
rows = [(1, "a"), (2, "b")]
print(rows.index((2, "b")))
print((1, "a") in rows)
''')


if __name__ == "__main__":
    raise SystemExit(main(CASES, "gen_elem_cases.py", sys.argv))
