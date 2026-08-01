# probes: writing an element through an alias is visible
# expect:
# 99
# [99, 2, 3]
a = [1, 2, 3]
b = a
b[0] = 99
print(a[0])
print(a)
