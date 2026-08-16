# expect:
# [2, 3] [1, 2, 3, 4]
a = frozenset([1, 2, 3])
b = frozenset([2, 3, 4])
print(sorted(a & b), sorted(a | b))
# asmpython (beta/3.14.0) MISMATCH: prints "['2', '3'] ['1', '2', '3', '4']\n" (wrong).
