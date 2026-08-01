# tier: cpython
# ref: library/stdtypes.html#mutable-sequence-types
# expect:
# [0, 2, 3]
# [0, 2, 3]
xs = [0, 1, 2, 3]
seen = []
for v in xs:
    seen.append(v)
    if v == 0:
        xs.remove(1)
print(seen)
print(xs)
