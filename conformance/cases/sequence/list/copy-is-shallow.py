# tier: spec
# ref: library/stdtypes.html#mutable-sequence-types
# expect:
# True False
# [[1, 2]] [[1, 2]]
# True
inner = [1]
xs = [inner]
ys = list(xs)
print(ys == xs, ys is xs)
inner.append(2)
print(xs, ys)
print(ys[0] is xs[0])
