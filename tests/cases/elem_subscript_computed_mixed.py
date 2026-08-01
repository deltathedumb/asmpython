# probes: a container element is read by computed index (mixed elements)
# expect:
# two
# 3.5
# None
xs = [1, "two", 3.5, True, None]
i = 1
print(xs[i])
print(xs[i + 1])
print(xs[len(xs) - 1])
