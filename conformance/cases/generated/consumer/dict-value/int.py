# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3
# [3, 1, 2]
xs = [3, 1, 2]
d = {'k': xs}
print(d['k'][0])
print(d['k'])
