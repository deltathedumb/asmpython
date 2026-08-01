# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# [0, 2, 4, 6]
# [1, 3, 5]
# [('a', 1), ('a', 2), ('b', 1), ('b', 2)]
# [['a', 'b'], ['c', 'd']]
print([v * 2 for v in range(4)])
print([v for v in range(6) if v % 2])
print([(a, b) for a in "ab" for b in (1, 2)])
print([[c for c in row] for row in ("ab", "cd")])
