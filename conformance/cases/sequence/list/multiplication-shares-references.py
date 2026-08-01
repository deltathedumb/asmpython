# tier: spec
# ref: library/stdtypes.html#common-sequence-operations
# expect:
# [[0, 1], [0, 1]]
# [[0, 1], [0]]
rows = [[0]] * 2
rows[0].append(1)
print(rows)
fresh = [[0] for _ in range(2)]
fresh[0].append(1)
print(fresh)
