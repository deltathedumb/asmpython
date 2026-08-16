# probes: a list comprehension reads each element (mixed elements)
# expect:
# [1, 'two', 3.5, True, None]
xs = [1, "two", 3.5, True, None]
print([v for v in xs])
