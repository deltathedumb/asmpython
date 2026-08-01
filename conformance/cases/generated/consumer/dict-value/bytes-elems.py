# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# [b'ab', b'cd']
xs = [b'ab', b'cd']
d = {'k': xs}
print(d['k'][0])
print(d['k'])
