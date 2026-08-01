# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1
# ['two', 3.5, True, None]
xs = [1, 'two', 3.5, True, None]
head = xs[0]
rest = xs[1:]
print(head)
print(rest)
