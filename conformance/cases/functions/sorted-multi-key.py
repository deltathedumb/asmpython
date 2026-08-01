# tier: spec
# ref: library/functions.html#sorted
# expect:
# [('b', 1), ('a', 2), ('b', 2)]
# [('b', 2), ('a', 2), ('b', 1)]
# ['a', 'a', 'a', 'b', 'n', 'n']
rows = [("b", 2), ("a", 2), ("b", 1)]
print(sorted(rows, key=lambda r: (r[1], r[0])))
print(sorted(rows, key=lambda r: r[1], reverse=True))
print(sorted("banana"))
