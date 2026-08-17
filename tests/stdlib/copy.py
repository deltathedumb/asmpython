# COVERAGE: copy and deepcopy over lists, dicts, tuples, sets, frozensets,
# bytearrays and user objects; the __copy__ and __deepcopy__ hooks; the memo,
# through a structure that SHARES a node and one that contains ITSELF; and
# that atomic and immutable values are their own copies. NOT covered here:
# __reduce__, __getstate__/__setstate__, copyreg, or copy.replace -- the
# module declares it has none of them.
#
# THE SHARING IS THE POINT. A deepcopy that merely produced equal output would
# pass an equality test and still be wrong: a structure whose two fields were
# the same list must come out with its two fields still the same list, and one
# that contains itself must not recurse forever.
import copy

xs = [1, [2, 3], {"k": [4]}]
shallow = copy.copy(xs)
deep = copy.deepcopy(xs)
print(shallow == xs, deep == xs)
print(shallow is xs, deep is xs)
# SHALLOW SHARES THE INSIDES, deep does not.
print(shallow[1] is xs[1], deep[1] is xs[1])
xs[1].append(99)
print(shallow[1], deep[1])

# A SHARED NODE STAYS SHARED. One object reachable twice must come out as one
# object, or a structure that shared a node stops sharing it.
inner = [1, 2]
pair = [inner, inner]
made = copy.deepcopy(pair)
print(made[0] is made[1], made[0] == [1, 2], made[0] is inner)

# A STRUCTURE THAT CONTAINS ITSELF terminates, which is the other half of what
# the memo is for.
loop = [1]
loop.append(loop)
got = copy.deepcopy(loop)
print(got[0], got[1] is got, len(got))

# The containers, one at a time.
print(copy.copy({"a": 1}), copy.deepcopy({"a": [1]}))
# THROUGH A VARIABLE, not a literal: `copy.copy((1,2)) is (1,2)` compares
# against a SECOND literal whose identity is CPython's interning accident, and
# conformance/TAXONOMY.md says not to pin those. The property being tested is
# that copying an immutable answers the same object.
tup = (1, 2)
print(copy.copy(tup) == tup, copy.copy(tup) is tup)
print(sorted(copy.deepcopy({1, 2, 3})))
print(sorted(copy.deepcopy(frozenset([1, 2]))))
print(copy.deepcopy(bytearray(b"ab")), copy.copy(bytearray(b"ab")))
d = {"outer": {"inner": [1]}}
dd = copy.deepcopy(d)
print(dd == d, dd["outer"] is d["outer"], dd["outer"]["inner"] is d["outer"]["inner"])

# ATOMIC VALUES ARE THEIR OWN COPIES -- there is nothing inside to copy.
for label, one in (("int", 1), ("float", 2.5), ("bool", True), ("None", None),
                   ("str", "s"), ("bytes", b"b"), ("Ellipsis", Ellipsis),
                   ("NotImplemented", NotImplemented), ("type", int)):
    print(label, copy.copy(one) is one, copy.deepcopy(one) is one)


# A USER OBJECT is rebuilt around a copy of its state, WITHOUT running
# __init__: the state is what is being copied.
class Node:
    def __init__(self, value, kids=None):
        self.value = value
        self.kids = kids if kids is not None else []
        self.built = True


n = Node(1, [Node(2)])
sn = copy.copy(n)
dn = copy.deepcopy(n)
print(sn.value, sn.kids is n.kids, sn.built)
print(dn.value, dn.kids is n.kids, dn.kids[0].value, dn.built)
print(type(dn) is Node, type(dn).__name__)


# THE HOOKS WIN when a class writes them.
class Custom:
    def __init__(self):
        self.tag = "original"

    def __copy__(self):
        made = Custom()
        made.tag = "shallow hook"
        return made

    def __deepcopy__(self, memo):
        made = Custom()
        made.tag = "deep hook"
        return made


print(copy.copy(Custom()).tag, copy.deepcopy(Custom()).tag)

# `issubclass` IS COMPARED NOW: the exception registration follows a class
# rename, so a bundled module's exception sits in the hierarchy under the name
# it calls itself. `type(e).__name__` still answers the mangled spelling for a
# RAISED one and is not compared -- that half is recorded in docs/STDLIB.md.
print(issubclass(copy.Error, Exception), copy.error is copy.Error)
try:
    raise copy.Error("x")
except Exception as exc:
    print("caught by Exception:", exc)
# A CLASS THAT REFUSES WRITES IS STILL COPYABLE. Rebuilding a copy is not
# mutating it, so the state goes in through `object.__setattr__` rather than
# `setattr` -- otherwise an immutable class raises from its own `__setattr__`
# while being CONSTRUCTED. Found by `dataclasses`, whose frozen classes are
# the ones every program has.
class Immutable:
    def __init__(self, k, xs):
        object.__setattr__(self, "k", k)
        object.__setattr__(self, "xs", xs)

    def __setattr__(self, name, value):
        raise AttributeError("cannot assign to field %r" % (name,))

    def __repr__(self):
        return "Immutable(%r, %r)" % (self.k, self.xs)


frozen_one = Immutable(3, [1, 2])
deep = copy.deepcopy(frozen_one)
shallow = copy.copy(frozen_one)
print(deep, deep.xs is frozen_one.xs, deep is frozen_one)
print(shallow, shallow.xs is frozen_one.xs)

print("done")
