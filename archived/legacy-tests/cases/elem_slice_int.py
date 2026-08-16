# probes: a slice copies a run of elements (int elements)
# expect:
# [20, 30]
# [10, 20]
# [30, 40]
xs = [10, 20, 30, 40]
print(xs[1:3])
print(xs[:2])
print(xs[-2:])
