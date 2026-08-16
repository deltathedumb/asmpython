# probes: zip() reads elements from two containers (int elements)
# expect:
# 10 10
# 20 20
# 30 30
# 40 40
xs = [10, 20, 30, 40]
ys = [10, 20, 30, 40]
for a, b in zip(xs, ys):
    print(a, b)
