# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [1, 2]
# [[3], [4, 5, 6]]
xs = [[1, 2], [3], [4, 5, 6]]
head = xs[0]
rest = xs[1:]
print(head)
print(rest)
