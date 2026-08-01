# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# True
# [True, False, True]
xs = [True, False, True]
d = {'k': xs}
print(d['k'][0])
print(d['k'])
