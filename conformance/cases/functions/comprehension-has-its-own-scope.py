# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# [0, 1, 2]
# outer
x = "outer"
vals = [x for x in range(3)]
print(vals)
print(x)
