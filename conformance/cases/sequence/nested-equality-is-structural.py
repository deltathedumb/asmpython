# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# True
# False
# False
# False
a = [[1], {"k": (2, 3)}]
b = [[1], {"k": (2, 3)}]
print(a == b)
print(a is b)
print(a[0] is b[0])
print([1, 2] == (1, 2))
