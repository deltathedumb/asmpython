# probes: min/max with key= read each element (mixed elements)
# expect:
# 1
# two
xs = [1, "two", 3.5, True, None]
print(min(xs, key=str))
print(max(xs, key=str))
