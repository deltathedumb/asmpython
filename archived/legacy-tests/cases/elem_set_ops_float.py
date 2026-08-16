# probes: set operations compare elements (float elements)
# expect:
# [1.5, 2.5, 3.5, 5.5]
# [2.5, 3.5]
# [1.5]
a = set([1.5, 2.5, 3.5])
b = set([2.5, 3.5, 5.5])
print(sorted(a | b, key=str))
print(sorted(a & b, key=str))
print(sorted(a - b, key=str))
