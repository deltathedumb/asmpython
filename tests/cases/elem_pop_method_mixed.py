# probes: list.pop returns the removed element (mixed elements)
# expect:
# None
# 1
# ['two', 3.5, True]
xs = list([1, "two", 3.5, True, None])
print(xs.pop())
print(xs.pop(0))
print(xs)
