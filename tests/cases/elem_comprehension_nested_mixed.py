# probes: a nested comprehension reads inner elements (mixed elements)
# expect:
# [[1, 'two', 3.5], [1, 'two', 3.5]]
rows = [[1, "two", 3.5], [1, "two", 3.5]]
print([[v for v in row] for row in rows])
