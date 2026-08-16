# probes: sorted(key=) reads each element (mixed elements)
# expect:
# [1, 3.5, None, True, 'two']
xs = [1, "two", 3.5, True, None]
print(sorted(xs, key=str))
