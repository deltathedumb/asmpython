# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1, 'a')
# [(2, 'b')]
xs = [(1, 'a'), (2, 'b')]
head = xs[0]
rest = xs[1:]
print(head)
print(rest)
