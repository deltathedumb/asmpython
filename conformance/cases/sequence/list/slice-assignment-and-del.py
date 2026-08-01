# tier: spec
# ref: library/stdtypes.html#mutable-sequence-types
# expect:
# [0, 'a', 3, 4]
# ['s', 0, 'a', 3, 4]
# [0, 'a', 3, 4]
# [0]
xs = [0, 1, 2, 3, 4]
xs[1:3] = ["a"]
print(xs)
xs[:0] = ["s"]
print(xs)
del xs[0]
print(xs)
del xs[1:]
print(xs)
