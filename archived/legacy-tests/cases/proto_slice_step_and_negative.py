# probes: extended slices honour step and negatives
# expect:
# [0, 2, 4]
# [5, 4, 3, 2, 1, 0]
# [4, 5]
# [1, 2, 3, 4]
xs = [0, 1, 2, 3, 4, 5]
print(xs[::2])
print(xs[::-1])
print(xs[-2:])
print(xs[1:-1])
