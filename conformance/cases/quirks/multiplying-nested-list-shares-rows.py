# tier: cpython
# ref: library/stdtypes.html#common-sequence-operations
# expect:
# [[9, 0], [9, 0]]
# [[9, 0], [0, 0]]
grid = [[0] * 2] * 2
grid[0][0] = 9
print(grid)
grid2 = [[0] * 2 for _ in range(2)]
grid2[0][0] = 9
print(grid2)
