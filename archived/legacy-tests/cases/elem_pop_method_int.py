# probes: list.pop returns the removed element (int elements)
# expect:
# 40
# 10
# [20, 30]
xs = list([10, 20, 30, 40])
print(xs.pop())
print(xs.pop(0))
print(xs)
