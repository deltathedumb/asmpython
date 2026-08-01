# probes: a slice copies a run of elements (mixed elements)
# expect:
# ['two', 3.5]
# [1, 'two']
# [True, None]
xs = [1, "two", 3.5, True, None]
print(xs[1:3])
print(xs[:2])
print(xs[-2:])
