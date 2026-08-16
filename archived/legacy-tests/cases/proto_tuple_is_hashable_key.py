# probes: a tuple works as a dict key
# expect:
# a
# 2
grid = {}
grid[(0, 1)] = "a"
grid[(1, 0)] = "b"
print(grid[(0, 1)])
print(len(grid))
