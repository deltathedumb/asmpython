# probes: mutating an inner list through the outer container
# expect:
# [[1, 2, 9], [3, 4]]
# 3
grid = [[1, 2], [3, 4]]
grid[0].append(9)
print(grid)
print(len(grid[0]))
