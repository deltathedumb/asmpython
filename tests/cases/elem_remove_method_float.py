# probes: list.remove deletes an element by value (float elements)
# expect:
# [1.5, 3.5, 4.5]
# 3
xs = list([1.5, 2.5, 3.5, 4.5])
xs.remove(2.5)
print(xs)
print(len(xs))
