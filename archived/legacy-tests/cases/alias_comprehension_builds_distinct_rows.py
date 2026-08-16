# probes: a comprehension builds distinct rows
# expect:
# [[0, 1], [0], [0]]
# 1
grid = [[0] for _ in range(3)]
grid[0].append(1)
print(grid)
print(len(grid[1]))
