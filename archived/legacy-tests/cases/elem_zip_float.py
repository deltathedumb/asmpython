# probes: zip() reads elements from two containers (float elements)
# expect:
# 1.5 1.5
# 2.5 2.5
# 3.5 3.5
# 4.5 4.5
xs = [1.5, 2.5, 3.5, 4.5]
ys = [1.5, 2.5, 3.5, 4.5]
for a, b in zip(xs, ys):
    print(a, b)
