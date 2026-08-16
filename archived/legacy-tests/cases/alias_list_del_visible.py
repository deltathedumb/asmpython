# probes: deleting an element through an alias is visible
# expect:
# [1, 3]
a = [1, 2, 3]
b = a
del b[1]
print(a)
