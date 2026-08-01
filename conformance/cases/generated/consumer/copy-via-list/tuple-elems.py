# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# [(1, 'a'), (2, 'b')]
# True
xs = [(1, 'a'), (2, 'b')]
ys = list(xs)
print(ys)
print(ys == xs)
