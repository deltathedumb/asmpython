# probes: a container element is read by computed index (int elements)
# expect:
# 20
# 30
# 40
xs = [10, 20, 30, 40]
i = 1
print(xs[i])
print(xs[i + 1])
print(xs[len(xs) - 1])
