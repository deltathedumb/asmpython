# probes: an extended slice copies with a step (int elements)
# expect:
# [10, 30]
# [40, 30, 20, 10]
# [20, 40]
xs = [10, 20, 30, 40]
print(xs[::2])
print(xs[::-1])
print(xs[1::2])
