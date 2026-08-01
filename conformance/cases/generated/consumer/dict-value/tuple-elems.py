# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# (1, 'a')
# [(1, 'a'), (2, 'b')]
xs = [(1, 'a'), (2, 'b')]
d = {'k': xs}
print(d['k'][0])
print(d['k'])
