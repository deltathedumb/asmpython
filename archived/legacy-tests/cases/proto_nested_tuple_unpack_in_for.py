# probes: a for target can destructure nested pairs
# expect:
# 1 a b
# 2 c d
pairs = [(1, ("a", "b")), (2, ("c", "d"))]
for number, (left, right) in pairs:
    print(number, left, right)
