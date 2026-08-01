# tier: spec
# ref: library/stdtypes.html#mutable-sequence-types
# expect:
# [0, 1, 2, 3]
# [0, 1, 2, 3, 9]
# [0, 1, 2, 3, 8, 9]
# 0 9 [1, 2, 3, 8]
# IndexError
xs = [1, 2, 3]
xs.insert(0, 0)
print(xs)
xs.insert(100, 9)
print(xs)
xs.insert(-1, 8)
print(xs)
print(xs.pop(0), xs.pop(-1), xs)
try:
    [].pop()
except IndexError:
    print("IndexError")
