# probes: d[k] += v reads, adds and stores back
# expect:
# 6
# [11, 2]
counts = {"a": 1}
counts["a"] += 5
print(counts["a"])
xs = [1, 2]
xs[0] += 10
print(xs)
