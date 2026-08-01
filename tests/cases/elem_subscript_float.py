# probes: a container element is read by literal index (float elements)
# expect:
# 1.5
# 2.5
# 4.5
xs = [1.5, 2.5, 3.5, 4.5]
print(xs[0])
print(xs[1])
print(xs[-1])
