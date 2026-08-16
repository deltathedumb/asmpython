# probes: slice assignment through an alias is visible
# expect:
# [1, 'x', 4]
a = [1, 2, 3, 4]
b = a
b[1:3] = ["x"]
print(a)
