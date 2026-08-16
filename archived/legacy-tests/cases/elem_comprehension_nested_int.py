# probes: a nested comprehension reads inner elements (int elements)
# expect:
# [[10, 20, 30], [10, 20, 30]]
rows = [[10, 20, 30], [10, 20, 30]]
print([[v for v in row] for row in rows])
