# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'ab'
# b'cd'
xs = [b'ab', b'cd']
print(min(xs, key=repr))
print(max(xs, key=repr))
