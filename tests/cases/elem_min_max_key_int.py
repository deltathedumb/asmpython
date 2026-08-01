# probes: min/max with key= read each element (int elements)
# expect:
# 10
# 40
xs = [10, 20, 30, 40]
print(min(xs, key=str))
print(max(xs, key=str))
