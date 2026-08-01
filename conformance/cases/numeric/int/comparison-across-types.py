# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# True
# False
# True
# [1.5, 2, 3]
print(1 == 1.0)
print(1 is 1.0)
print(2 ** 60 == float(2 ** 60))
print(sorted([2, 1.5, 3]))
