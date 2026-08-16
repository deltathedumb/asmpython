# probes: an extended slice copies with a step (mixed elements)
# expect:
# [1, 3.5, None]
# [None, True, 3.5, 'two', 1]
# ['two', True]
xs = [1, "two", 3.5, True, None]
print(xs[::2])
print(xs[::-1])
print(xs[1::2])
