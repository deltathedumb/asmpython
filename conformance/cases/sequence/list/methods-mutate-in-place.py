# tier: spec
# ref: library/stdtypes.html#mutable-sequence-types
# expect:
# None
# [0, 1, 2, 3, 4]
# 1 2
# [4, 3, 2, 1, 0]
# []
xs = [1, 2]
print(xs.append(3))
xs.insert(0, 0)
xs.extend([4])
print(xs)
print(xs.count(1), xs.index(2))
xs.reverse()
print(xs)
xs.clear()
print(xs)
