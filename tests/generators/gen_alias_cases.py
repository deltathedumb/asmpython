"""Generate aliasing, mutation and identity probes.

Python's assignment statement never copies. `b = a` binds a second name to the
same object, and every mutation through either name is visible through both.
Nothing in the corpus tested that. The 1628 cases before this file could all
pass on an implementation that copied a list on assignment, because a program
that never observes the difference cannot report it -- and a compiler whose
values are 64-bit words is under constant pressure to copy, because copying a
word is what the machine does by default.

That makes this the shortest path in the corpus to a real miscompile. A wrong
answer here is not a missing feature that refuses to compile; it is a program
that runs, prints a plausible number, and is wrong. Every other area fails
loudly.

The probes separate three things that are easy to fuse:

* **aliasing** -- two names, one object, mutation visible through both
* **copying** -- the operations that DO produce a new object (`a[:]`, `list(a)`,
  `dict(a)`), and how far the copy goes (one level; the inner objects stay
  shared)
* **identity** -- `is` vs `==`, and whether identity survives a function call,
  a container round trip, and an opaque parameter

Two probes deliberately pin a CPython *implementation detail* rather than a
language guarantee: the small-integer cache and string interning
(`alias_is_small_int_cached`, `alias_is_interned_string`). They are marked as
such in their probe text. They are included because a value model that boxes
integers will diverge here first, and the divergence is worth seeing even
though "correct" is arguable -- CPython's own answer is the corpus's
definition of correct everywhere else.

Usage: python gen_alias_cases.py <tests/cases dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _emit import CaseSet, main  # noqa: E402

CASES = CaseSet("probes")
case = CASES.case


# ---------------------------------------------------------------------------
# mutation is visible through every alias
# ---------------------------------------------------------------------------

case("alias_list_append_visible", "appending through an alias is visible", r'''
a = [1, 2]
b = a
b.append(3)
print(len(a))
print(a)
print(a == b)
''')

case("alias_list_element_write_visible", "writing an element through an alias is visible", r'''
a = [1, 2, 3]
b = a
b[0] = 99
print(a[0])
print(a)
''')

case("alias_list_clear_visible", "clearing through an alias empties both names", r'''
a = [1, 2, 3]
b = a
b.clear()
print(len(a))
print(a)
''')

case("alias_list_sort_visible", "sorting in place through an alias reorders both", r'''
a = [3, 1, 2]
b = a
b.sort()
print(a)
''')

case("alias_list_del_visible", "deleting an element through an alias is visible", r'''
a = [1, 2, 3]
b = a
del b[1]
print(a)
''')

case("alias_list_slice_write_visible", "slice assignment through an alias is visible", r'''
a = [1, 2, 3, 4]
b = a
b[1:3] = ["x"]
print(a)
''')

case("alias_dict_insert_visible", "inserting through a dict alias is visible", r'''
a = {"k": 1}
b = a
b["new"] = 2
print(len(a))
print(a["new"])
''')

case("alias_dict_delete_visible", "deleting through a dict alias is visible", r'''
a = {"k": 1, "j": 2}
b = a
del b["k"]
print(len(a))
print("k" in a)
''')

case("alias_dict_update_visible", "update() through an alias is visible", r'''
a = {"k": 1}
b = a
b.update({"k": 9, "j": 2})
print(a["k"])
print(len(a))
''')

case("alias_set_add_visible", "adding through a set alias is visible", r'''
a = {1, 2}
b = a
b.add(3)
print(len(a))
print(sorted(a))
''')

case("alias_nested_inner_list_shared", "an inner list is shared with its container", r'''
inner = [1]
outer = [inner]
inner.append(2)
print(outer)
print(len(outer[0]))
''')

case("alias_dict_value_is_shared", "a list stored as a dict value stays shared", r'''
items = [1]
holder = {"items": items}
items.append(2)
print(holder["items"])
''')

case("alias_instance_field_shared", "a list stored in a field stays shared", r'''
class Box:
    def __init__(self, items):
        self.items = items


items = [1]
box = Box(items)
items.append(2)
print(box.items)
print(len(box.items))
''')

case("alias_two_names_for_instance", "two names for one instance see one state", r'''
class Counter:
    def __init__(self):
        self.n = 0


a = Counter()
b = a
b.n = 5
print(a.n)
''')

case("alias_instance_in_list_shared", "mutating an instance through a list element", r'''
class Counter:
    def __init__(self):
        self.n = 0


c = Counter()
holder = [c]
holder[0].n = 7
print(c.n)
''')

case("alias_list_in_two_containers", "one list reachable from two containers", r'''
shared = [1]
left = [shared]
right = {"v": shared}
shared.append(2)
print(left[0])
print(right["v"])
print(len(left[0]))
''')


# ---------------------------------------------------------------------------
# what actually copies, and how far
# ---------------------------------------------------------------------------

case("alias_slice_copy_is_independent", "a full slice produces an independent list", r'''
a = [1, 2]
b = a[:]
b.append(3)
print(a)
print(b)
print(a == b)
''')

case("alias_list_constructor_copies", "list(a) produces an independent list", r'''
a = [1, 2]
b = list(a)
b.append(3)
print(a)
print(b)
''')

case("alias_dict_constructor_copies", "dict(a) produces an independent dict", r'''
a = {"k": 1}
b = dict(a)
b["new"] = 2
print(len(a))
print(len(b))
''')

case("alias_dict_copy_method", "dict.copy produces an independent dict", r'''
a = {"k": 1}
b = a.copy()
b["k"] = 9
print(a["k"])
print(b["k"])
''')

case("alias_set_constructor_copies", "set(a) produces an independent set", r'''
a = {1, 2}
b = set(a)
b.add(3)
print(len(a))
print(len(b))
''')

case("alias_shallow_copy_shares_inner", "a shallow copy still shares the inner objects", r'''
inner = [1]
a = [inner]
b = a[:]
b[0].append(2)
print(a[0])
print(len(a[0]))
''')

case("alias_copy_module_shallow", "copy.copy is one level deep", r'''
import copy

inner = [1]
a = {"in": inner}
b = copy.copy(a)
b["in"].append(2)
b["new"] = 1
print(a["in"])
print(len(a))
''')

case("alias_deepcopy_shares_nothing", "copy.deepcopy shares nothing", r'''
import copy

a = {"in": [1]}
b = copy.deepcopy(a)
b["in"].append(2)
print(a["in"])
print(b["in"])
''')

case("alias_list_multiply_shares_rows", "[[0]] * n repeats ONE row, not n rows", r'''
grid = [[0]] * 3
grid[0].append(1)
print(grid)
print(len(grid[1]))
''')

case("alias_comprehension_builds_distinct_rows", "a comprehension builds distinct rows", r'''
grid = [[0] for _ in range(3)]
grid[0].append(1)
print(grid)
print(len(grid[1]))
''')

case("alias_tuple_holds_mutable_element", "a tuple is immutable but its elements are not", r'''
holder = ([],)
holder[0].append(1)
print(holder)
print(len(holder[0]))
''')

case("alias_str_rebinding_does_not_mutate", "str is immutable; += rebinds", r'''
s = "a"
t = s
t += "b"
print(s)
print(t)
''')

case("alias_int_rebinding_does_not_mutate", "int is immutable; += rebinds", r'''
n = 1
m = n
m += 1
print(n)
print(m)
''')

case("alias_augmented_list_mutates_tuple_rebinds", "xs += mutates a list, rebinds a tuple", r'''
xs = [1]
xs_alias = xs
xs += [2]
print(xs_alias)

t = (1,)
t_alias = t
t += (2,)
print(t_alias)
print(t)
''')


# ---------------------------------------------------------------------------
# is vs ==
# ---------------------------------------------------------------------------

case("alias_is_true_for_same_object", "`is` is True for two names of one object", r'''
a = [1]
b = a
print(a is b)
print(a == b)
''')

case("alias_is_false_for_equal_lists", "two equal lists are not the same object", r'''
a = [1]
b = [1]
print(a is b)
print(a == b)
print(a is not b)
''')

case("alias_is_false_for_equal_dicts", "two equal dicts are not the same object", r'''
a = {"k": 1}
b = {"k": 1}
print(a is b)
print(a == b)
''')

case("alias_is_false_for_equal_instances", "two instances are distinct objects", r'''
class Plain:
    pass


print(Plain() is Plain())
a = Plain()
print(a is a)
''')

case("alias_is_none_singleton", "None is a singleton", r'''
a = None
b = None
print(a is b)
print(a is None)
print(a == None)
''')

case("alias_is_bool_singletons", "True and False are singletons distinct from 1 and 0", r'''
print(True is True)
print(False is False)
print(1 == True)
print(1 is True)
print(0 == False)
''')

# NOT a language guarantee -- CPython caches the ints in [-5, 256], so two
# separately COMPUTED small ints are the same object and two large ones are
# not. Pinned deliberately: a boxed-integer value model diverges here first.
case("alias_is_small_int_cached", "CPython caches small ints (implementation detail)", r'''
a = 1
b = int("1")
print(a == b)
print(a is b)

big = 1000
other = int("1000")
print(big == other)
print(big is other)
''')

# Also an implementation detail: a literal is interned, a runtime-built string
# is not, so `is` separates them while `==` does not.
case("alias_is_interned_string", "a runtime-built str is a distinct object", r'''
a = "hello"
b = "".join(["hel", "lo"])
print(a == b)
print(a is b)
''')

case("alias_eq_does_not_imply_is", "equality never implies identity", r'''
a = [1, 2]
b = [1, 2]
print(a == b and a is not b)
''')


# ---------------------------------------------------------------------------
# identity across a call boundary -- where a boxing convention breaks first
# ---------------------------------------------------------------------------

case("alias_identity_survives_passthrough", "a returned argument is the same object", r'''
def passthrough(v):
    return v


a = [1]
print(passthrough(a) is a)
''')

case("alias_mutation_inside_function_visible", "a function mutates the caller's list", r'''
def add(xs):
    xs.append(2)


a = [1]
add(a)
print(a)
print(len(a))
''')

case("alias_rebinding_inside_function_not_visible", "rebinding a parameter does not reach the caller", r'''
def rebind(xs):
    xs = [9, 9]
    return xs


a = [1]
result = rebind(a)
print(a)
print(result)
''')

case("alias_mutation_through_object_annotation", "an object-annotated parameter still aliases", r'''
def add(xs: object) -> object:
    xs.append(2)
    return xs


a = [1]
returned = add(a)
print(a)
print(returned is a)
''')

case("alias_instance_mutated_in_function", "a function mutates the caller's instance", r'''
class Counter:
    def __init__(self):
        self.n = 0


def bump(c):
    c.n = c.n + 1


c = Counter()
bump(c)
bump(c)
print(c.n)
''')

case("alias_identity_through_two_hops", "identity survives two call hops", r'''
def inner(v):
    return v


def outer(v):
    return inner(v)


a = {"k": 1}
print(outer(a) is a)
''')

case("alias_identity_through_container_roundtrip", "identity survives a container round trip", r'''
a = [1]
box = [a]
print(box[0] is a)
holder = {"v": a}
print(holder["v"] is a)
''')

case("alias_identity_returned_from_method", "a field read back is the same object", r'''
class Box:
    def __init__(self, payload):
        self.payload = payload

    def get(self):
        return self.payload


items = [1]
box = Box(items)
print(box.get() is items)
print(box.payload is items)
''')

case("alias_mutation_through_returned_field", "mutating a returned field reaches the owner", r'''
class Box:
    def __init__(self):
        self.items = []

    def get(self):
        return self.items


box = Box()
box.get().append(1)
box.get().append(2)
print(box.items)
print(len(box.items))
''')

case("alias_identity_through_list_of_instances", "an instance read from a list is the same object", r'''
class Node:
    def __init__(self, tag):
        self.tag = tag


first = Node("a")
nodes = [first, Node("b")]
print(nodes[0] is first)
nodes[0].tag = "changed"
print(first.tag)
''')


# ---------------------------------------------------------------------------
# default arguments: one object, shared across every call
# ---------------------------------------------------------------------------

case("alias_default_list_shared_across_calls", "a mutable default persists between calls", r'''
def collect(value, into=[]):
    into.append(value)
    return into


print(collect(1))
print(collect(2))
print(collect(3))
''')

case("alias_default_dict_shared_across_calls", "a mutable default dict persists between calls", r'''
def record(key, into={}):
    into[key] = len(into)
    return len(into)


print(record("a"))
print(record("b"))
''')

case("alias_default_is_one_object", "every call sees the same default object", r'''
def get_default(into=[]):
    return into


print(get_default() is get_default())
''')


# ---------------------------------------------------------------------------
# self-reference and cycles
# ---------------------------------------------------------------------------

case("alias_self_referential_list", "a list containing itself renders as [...]", r'''
a = [1]
a.append(a)
print(len(a))
print(a[1] is a)
print(a)
''')

case("alias_self_referential_dict", "a dict containing itself renders as {...}", r'''
d = {"n": 1}
d["self"] = d
print(len(d))
print(d["self"] is d)
print(d)
''')

case("alias_two_object_cycle", "two instances may reference each other", r'''
class Node:
    def __init__(self, tag):
        self.tag = tag
        self.other = None


a = Node("a")
b = Node("b")
a.other = b
b.other = a
print(a.other.tag)
print(a.other.other is a)
print(b.other.other.tag)
''')

case("alias_nested_self_reference_depth", "a cycle is reachable at arbitrary depth", r'''
a = []
a.append(a)
print(a[0][0][0] is a)
''')


# ---------------------------------------------------------------------------
# id()
# ---------------------------------------------------------------------------

case("alias_id_stable_for_one_object", "id() is stable for a live object", r'''
a = [1]
first = id(a)
a.append(2)
print(id(a) == first)
''')

case("alias_id_agrees_for_two_names", "two names for one object share an id", r'''
a = [1]
b = a
print(id(a) == id(b))
''')

case("alias_id_differs_for_distinct_objects", "distinct objects have distinct ids", r'''
a = [1]
b = [1]
print(id(a) == id(b))
print(a == b)
''')

case("alias_id_matches_is", "id() equality agrees with `is`", r'''
a = {"k": 1}
b = a
c = {"k": 1}
print((a is b) == (id(a) == id(b)))
print((a is c) == (id(a) == id(c)))
''')


# ===========================================================================
# Round 3. Round 2 found that a list mutated through a PARAMETER does not
# reach the caller -- `append` and `extend` are lost, while `xs[0] = 99` does
# write through. That located the defect (the data pointer is shared, the
# length is not) but not its boundary.
#
# These probes map the boundary, because the boundary is what says whether a
# fix is complete. Each one is a single mutating operation applied through a
# parameter, grouped by whether it changes the container's LENGTH:
#
#   length-changing : append extend insert pop remove clear slice-assign
#                     dict __setitem__/pop/clear/update, set add/discard
#   length-preserving: __setitem__ sort reverse
#
# If the length-preserving ones all pass and the length-changing ones all
# fail, the diagnosis is confirmed and the fix has an exact acceptance set.
# `alias_param_returns_mutated_list` is the sharpest of them: it asks whether
# the CALLEE's own view has the update the caller is missing.
# ===========================================================================

case("alias_param_append_lost", "append through a parameter reaches the caller", r'''
def mutate(xs):
    xs.append(2)


a = [1]
mutate(a)
print(len(a))
print(a)
''')

case("alias_param_returns_mutated_list", "the callee's own view of the mutation", r'''
def mutate(xs):
    xs.append(2)
    return xs


a = [1]
returned = mutate(a)
print(len(returned))
print(returned)
print(len(a))
print(returned is a)
''')

case("alias_param_insert", "insert through a parameter reaches the caller", r'''
def mutate(xs):
    xs.insert(0, 0)


a = [1]
mutate(a)
print(a)
''')

case("alias_param_pop", "pop through a parameter reaches the caller", r'''
def mutate(xs):
    return xs.pop()


a = [1, 2]
print(mutate(a))
print(a)
''')

case("alias_param_remove", "remove through a parameter reaches the caller", r'''
def mutate(xs):
    xs.remove(1)


a = [1, 2]
mutate(a)
print(a)
''')

case("alias_param_clear", "clear through a parameter reaches the caller", r'''
def mutate(xs):
    xs.clear()


a = [1, 2]
mutate(a)
print(len(a))
print(a)
''')

case("alias_param_slice_assign", "slice assignment through a parameter", r'''
def mutate(xs):
    xs[0:1] = [7, 8]


a = [1, 2]
mutate(a)
print(a)
''')

case("alias_param_setitem", "element assignment through a parameter", r'''
def mutate(xs):
    xs[0] = 99


a = [1, 2]
mutate(a)
print(a)
''')

case("alias_param_setitem_str", "element assignment of a str through a parameter", r'''
def mutate(xs):
    xs[0] = "replaced"


a = ["first", "second"]
mutate(a)
print(a)
print(a[0])
''')

case("alias_param_sort", "sort through a parameter reaches the caller", r'''
def mutate(xs):
    xs.sort()


a = [3, 1, 2]
mutate(a)
print(a)
''')

case("alias_param_reverse", "reverse through a parameter reaches the caller", r'''
def mutate(xs):
    xs.reverse()


a = [1, 2, 3]
mutate(a)
print(a)
''')

case("alias_param_extend", "extend through a parameter reaches the caller", r'''
def mutate(xs):
    xs.extend([2, 3])


a = [1]
mutate(a)
print(len(a))
print(a)
''')

case("alias_param_augmented_add", "xs += through a parameter reaches the caller", r'''
def mutate(xs):
    xs += [2]


a = [1]
mutate(a)
print(a)
''')

case("alias_param_dict_setitem", "dict insert through a parameter", r'''
def mutate(d):
    d["new"] = 1


a = {"k": 0}
mutate(a)
print(len(a))
print(sorted(a.keys()))
''')

case("alias_param_dict_pop", "dict pop through a parameter", r'''
def mutate(d):
    return d.pop("k")


a = {"k": 5, "j": 6}
print(mutate(a))
print(len(a))
''')

case("alias_param_dict_clear", "dict clear through a parameter", r'''
def mutate(d):
    d.clear()


a = {"k": 1}
mutate(a)
print(len(a))
''')

case("alias_param_dict_update", "dict update through a parameter", r'''
def mutate(d):
    d.update({"j": 2})


a = {"k": 1}
mutate(a)
print(len(a))
''')

case("alias_param_set_add", "set add through a parameter", r'''
def mutate(s):
    s.add(3)


a = {1, 2}
mutate(a)
print(len(a))
print(sorted(a))
''')

case("alias_param_set_discard", "set discard through a parameter", r'''
def mutate(s):
    s.discard(1)


a = {1, 2}
mutate(a)
print(len(a))
print(sorted(a))
''')

case("alias_param_inner_list", "mutating an inner list through a parameter", r'''
def mutate(rows):
    rows[0].append(9)


a = [[1], [2]]
mutate(a)
print(a)
print(len(a[0]))
''')

case("alias_param_two_hops", "mutation survives two parameter hops", r'''
def inner(xs):
    xs.append(3)


def outer(xs):
    inner(xs)


a = [1]
outer(a)
print(a)
''')

case("alias_param_then_local_alias", "a local alias of a parameter still aliases", r'''
def mutate(xs):
    local = xs
    local.append(2)


a = [1]
mutate(a)
print(a)
''')

case("alias_param_global_list", "a function mutates a global list without a parameter", r'''
shared = [1]


def mutate():
    shared.append(2)


mutate()
print(shared)
''')

case("alias_param_field_holding_list", "mutating a list held by a parameter's field", r'''
class Box:
    def __init__(self):
        self.items = [1]


def mutate(box):
    box.items.append(2)


b = Box()
mutate(b)
print(b.items)
''')

case("alias_param_rebind_field", "rebinding a parameter's field reaches the caller", r'''
class Box:
    def __init__(self):
        self.items = [1]


def replace(box):
    box.items = [7, 8]


b = Box()
replace(b)
print(b.items)
''')

case("alias_returned_list_is_mutable", "a returned list can be mutated by the caller", r'''
def build():
    return [1]


made = build()
made.append(2)
print(made)
print(len(made))
''')

case("alias_list_copy_method", "list.copy produces an independent list", r'''
a = [1, 2]
b = a.copy()
b.append(3)
print(a)
print(b)
''')

case("alias_sorted_returns_new_list", "sorted() does not alias its input", r'''
a = [3, 1]
b = sorted(a)
b.append(9)
print(a)
print(b)
''')

case("alias_dict_setdefault_returns_stored", "setdefault returns the stored object", r'''
groups = {}
first = groups.setdefault("k", [])
first.append(1)
print(groups["k"])
print(groups.setdefault("k", []) is first)
''')

case("alias_dict_get_returns_stored", "dict.get returns the stored object, not a copy", r'''
groups = {"k": [1]}
got = groups.get("k")
got.append(2)
print(groups["k"])
''')

case("alias_element_augmented_assign", "xs[0] += mutates in place through the subscript", r'''
counts = [1]
counts[0] += 5
print(counts)

rows = [[1]]
rows[0] += [2]
print(rows)
''')

case("alias_tuple_swap", "a, b = b, a swaps without aliasing", r'''
a = [1]
b = [2]
a, b = b, a
print(a)
print(b)
a.append(9)
print(b)
''')


if __name__ == "__main__":
    raise SystemExit(main(CASES, "gen_alias_cases.py", sys.argv))
