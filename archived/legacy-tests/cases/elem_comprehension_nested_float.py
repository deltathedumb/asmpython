# probes: a nested comprehension reads inner elements (float elements)
# expect:
# [[1.5, 2.5, 3.5], [1.5, 2.5, 3.5]]
rows = [[1.5, 2.5, 3.5], [1.5, 2.5, 3.5]]
print([[v for v in row] for row in rows])
