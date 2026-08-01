# tier: spec
# ref: library/stdtypes.html#frozenset
# expect:
# True
# False
# [1, 2, 3]
# frozenset
# set
# AttributeError
print({1}.isdisjoint({2}))
print({1}.isdisjoint({1}))
f = frozenset([1, 2])
print(sorted(f | {3}))
print(type(f | {3}).__name__)
print(type({3} | f).__name__)
try:
    f.add(3)
except AttributeError:
    print("AttributeError")
