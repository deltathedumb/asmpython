# probes: list.index finds an element by value (mixed elements)
# expect:
# 1
xs = list([1, "two", 3.5, True, None])
print(xs.index("two"))
