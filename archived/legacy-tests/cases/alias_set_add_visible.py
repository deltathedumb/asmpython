# probes: adding through a set alias is visible
# expect:
# 3
# [1, 2, 3]
a = {1, 2}
b = a
b.add(3)
print(len(a))
print(sorted(a))
