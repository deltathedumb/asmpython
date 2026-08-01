# probes: min/max with key= read each element (float elements)
# expect:
# 1.5
# 4.5
xs = [1.5, 2.5, 3.5, 4.5]
print(min(xs, key=str))
print(max(xs, key=str))
