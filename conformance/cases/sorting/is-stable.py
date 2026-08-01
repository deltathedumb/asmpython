# tier: spec
# ref: library/stdtypes.html#list.sort
# expect:
# [('a', 2), ('a', 4), ('b', 1), ('b', 3)]
xs = [("b", 1), ("a", 2), ("b", 3), ("a", 4)]
print(sorted(xs, key=lambda p: p[0]))
