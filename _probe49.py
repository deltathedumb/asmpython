# probe49: generators, itertools-style, more advanced patterns
# iter() / next() on a list
xs = [10, 20, 30]

# any() / all() with complex predicates
print(any(x > 25 for x in xs))   # True (30 > 25)
print(all(x > 5 for x in xs))    # True (all > 5)
print(any(x > 100 for x in xs))  # False

# min/max with key=
pairs = [("a", 3), ("b", 1), ("c", 2)]
# min by second element
m = min(pairs, key=lambda p: p[1])
print(m[0])  # b

# zip with list()
a = [1, 2, 3]
b = ["x", "y", "z"]
zipped = list(zip(a, b))
print(zipped[0][0])  # 1
print(zipped[0][1])  # x
print(len(zipped))   # 3

# enumerate with list()
items = ["a", "b", "c"]
enumed = list(enumerate(items))
print(enumed[1][0])  # 1
print(enumed[1][1])  # b
