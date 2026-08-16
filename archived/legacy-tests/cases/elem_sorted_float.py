# probes: sorted() orders the elements (float elements)
# expect:
# [1.5, 2.5, 3.5, 4.5]
# [4.5, 3.5, 2.5, 1.5]
xs = [1.5, 2.5, 3.5, 4.5]
print(sorted(xs))
print(sorted(xs, reverse=True))
