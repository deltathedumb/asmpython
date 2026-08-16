# probes: a container element is read by computed index (float elements)
# expect:
# 2.5
# 3.5
# 4.5
xs = [1.5, 2.5, 3.5, 4.5]
i = 1
print(xs[i])
print(xs[i + 1])
print(xs[len(xs) - 1])
