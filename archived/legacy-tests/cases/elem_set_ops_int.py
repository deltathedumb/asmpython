# probes: set operations compare elements (int elements)
# expect:
# [10, 20, 30, 50]
# [20, 30]
# [10]
a = set([10, 20, 30])
b = set([20, 30, 50])
print(sorted(a | b, key=str))
print(sorted(a & b, key=str))
print(sorted(a - b, key=str))
