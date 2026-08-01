# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# True
# False
# True
# True
# True
big = 2 ** 53
print(big == float(big))
print(big + 1 == float(big + 1))
print(big + 1 > float(big))
print(10 ** 400 > 1e308)
print(float("inf") > 10 ** 400)
