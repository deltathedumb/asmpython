# probes: [[0]] * n repeats ONE row, not n rows
# expect:
# [[0, 1], [0, 1], [0, 1]]
# 2
grid = [[0]] * 3
grid[0].append(1)
print(grid)
print(len(grid[1]))
