# probes: set operations compare elements (mixed elements)
# expect:
# [1, 3.5, 9, 'two']
# [3.5, 'two']
# [1]
a = set([1, "two", 3.5])
b = set(["two", 3.5, 9])
print(sorted(a | b, key=str))
print(sorted(a & b, key=str))
print(sorted(a - b, key=str))
