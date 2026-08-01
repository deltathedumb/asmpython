# probes: list.remove deletes an element by value (int elements)
# expect:
# [10, 30, 40]
# 3
xs = list([10, 20, 30, 40])
xs.remove(20)
print(xs)
print(len(xs))
