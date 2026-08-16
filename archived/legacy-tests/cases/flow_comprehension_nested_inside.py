# probes: a comprehension can contain a comprehension
# expect:
# [[2, 4], [6, 8]]
grid = [[1, 2], [3, 4]]
print([[cell * 2 for cell in row] for row in grid])
