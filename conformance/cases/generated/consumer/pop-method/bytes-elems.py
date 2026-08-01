# tier: spec
# ref: reference/datamodel.html#objects-values-and-types
# expect:
# b'cd'
# 1
xs = [b'ab', b'cd']
ys = list(xs)
print(ys.pop())
print(len(ys))
