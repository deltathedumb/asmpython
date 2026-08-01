# probes: sorted() orders the elements (int elements)
# expect:
# [10, 20, 30, 40]
# [40, 30, 20, 10]
xs = [10, 20, 30, 40]
print(sorted(xs))
print(sorted(xs, reverse=True))
