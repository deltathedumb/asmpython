# probes: appending through an alias is visible
# expect:
# 3
# [1, 2, 3]
# True
a = [1, 2]
b = a
b.append(3)
print(len(a))
print(a)
print(a == b)
