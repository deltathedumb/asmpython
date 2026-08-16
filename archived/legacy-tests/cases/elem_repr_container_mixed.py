# probes: the container renders its own elements (mixed elements)
# expect:
# [1, 'two', 3.5, True, None]
# [1, 'two', 3.5, True, None]
# [1, 'two', 3.5, True, None]
xs = [1, "two", 3.5, True, None]
print(xs)
print(repr(xs))
print(str(xs))
