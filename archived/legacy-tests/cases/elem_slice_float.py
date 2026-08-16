# probes: a slice copies a run of elements (float elements)
# expect:
# [2.5, 3.5]
# [1.5, 2.5]
# [3.5, 4.5]
xs = [1.5, 2.5, 3.5, 4.5]
print(xs[1:3])
print(xs[:2])
print(xs[-2:])
