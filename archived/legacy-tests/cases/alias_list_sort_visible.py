# probes: sorting in place through an alias reorders both
# expect:
# [1, 2, 3]
a = [3, 1, 2]
b = a
b.sort()
print(a)
