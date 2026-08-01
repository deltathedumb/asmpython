# tier: spec
# ref: reference/expressions.html#displays-for-lists-sets-and-dictionaries
# expect:
# [(0, 0), (0, 1), (1, 0), (1, 1)]
# [[0, 0, 0], [0, 1, 2]]
# [0, 2, 4]
# {0: 0, 1: 2, 2: 4}
# [0, 1]
print([(a, b) for a in range(2) for b in range(2)])
print([[a * b for b in range(3)] for a in range(2)])
print([a for a in range(5) if a % 2 == 0])
print({k: k * 2 for k in range(3)})
print(sorted({a % 2 for a in range(5)}))
