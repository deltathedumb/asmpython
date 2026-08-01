# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# 1
# [1, 'two', 3.5, True, None]
xs = [1, 'two', 3.5, True, None]
d = {'k': xs}
print(d['k'][0])
print(d['k'])
