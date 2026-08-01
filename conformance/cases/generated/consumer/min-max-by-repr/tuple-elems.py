# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1, 'a')
# (2, 'b')
xs = [(1, 'a'), (2, 'b')]
print(min(xs, key=repr))
print(max(xs, key=repr))
