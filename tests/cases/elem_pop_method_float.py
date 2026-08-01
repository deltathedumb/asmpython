# probes: list.pop returns the removed element (float elements)
# expect:
# 4.5
# 1.5
# [2.5, 3.5]
xs = list([1.5, 2.5, 3.5, 4.5])
print(xs.pop())
print(xs.pop(0))
print(xs)
