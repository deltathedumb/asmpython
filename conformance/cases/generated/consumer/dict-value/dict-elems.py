# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# {'a': 1}
# [{'a': 1}, {'b': 2}]
xs = [{'a': 1}, {'b': 2}]
d = {'k': xs}
print(d['k'][0])
print(d['k'])
