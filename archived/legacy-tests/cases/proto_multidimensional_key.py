# probes: d[a, b] passes a tuple key
# expect:
# cell
# [(1, 2)]
grid = {}
grid[1, 2] = "cell"
print(grid[(1, 2)])
print(list(grid.keys()))
