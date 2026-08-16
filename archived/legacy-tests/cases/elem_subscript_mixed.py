# probes: a container element is read by literal index (mixed elements)
# expect:
# 1
# two
# None
xs = [1, "two", 3.5, True, None]
print(xs[0])
print(xs[1])
print(xs[-1])
