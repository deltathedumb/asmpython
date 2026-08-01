# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [(2, 'b'), (1, 'a')]
xs = [(1, 'a'), (2, 'b')]
print(list(reversed(xs)))
