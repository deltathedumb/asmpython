# tier: spec
# ref: library/stdtypes.html#set
# expect:
# [1, 2] 2
# set frozenset
# [1, 2, 3]
# True
# False
s = set([1, 2, 2])
print(sorted(s), len(s))
print(type(set()).__name__, type(frozenset()).__name__)
print(sorted({1, 2} | {3}))
print(set() == frozenset())
print(bool(set()))
