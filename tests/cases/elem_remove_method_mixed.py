# probes: list.remove deletes an element by value (mixed elements)
# expect:
# [1, 3.5, True, None]
# 4
xs = list([1, "two", 3.5, True, None])
xs.remove("two")
print(xs)
print(len(xs))
