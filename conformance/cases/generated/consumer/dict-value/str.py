# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# c
# ['c', 'a', 'b']
xs = ['c', 'a', 'b']
d = {'k': xs}
print(d['k'][0])
print(d['k'])
