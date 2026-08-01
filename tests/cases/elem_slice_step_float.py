# probes: an extended slice copies with a step (float elements)
# expect:
# [1.5, 3.5]
# [4.5, 3.5, 2.5, 1.5]
# [2.5, 4.5]
xs = [1.5, 2.5, 3.5, 4.5]
print(xs[::2])
print(xs[::-1])
print(xs[1::2])
