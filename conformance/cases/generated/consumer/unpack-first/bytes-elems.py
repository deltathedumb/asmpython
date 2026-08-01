# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# [b'cd']
xs = [b'ab', b'cd']
head = xs[0]
rest = xs[1:]
print(head)
print(rest)
