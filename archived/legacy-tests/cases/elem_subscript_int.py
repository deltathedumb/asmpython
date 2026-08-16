# probes: a container element is read by literal index (int elements)
# expect:
# 10
# 20
# 40
xs = [10, 20, 30, 40]
print(xs[0])
print(xs[1])
print(xs[-1])
