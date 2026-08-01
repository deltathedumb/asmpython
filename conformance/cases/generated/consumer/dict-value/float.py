# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 3.5
# [3.5, 1.5, 2.5]
xs = [3.5, 1.5, 2.5]
d = {'k': xs}
print(d['k'][0])
print(d['k'])
