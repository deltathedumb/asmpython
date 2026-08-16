# probes: list.sort(key=) reads each element (mixed elements)
# expect:
# [1, 3.5, None, True, 'two']
xs = list([1, "two", 3.5, True, None])
xs.sort(key=str)
print(xs)
