# probe49b: without enumerate
xs = [10, 20, 30]

print(any(x > 25 for x in xs))   # True
print(all(x > 5 for x in xs))    # True
print(any(x > 100 for x in xs))  # False

# min with key=
pairs = [("a", 3), ("b", 1), ("c", 2)]
m = min(pairs, key=lambda p: p[1])
print(m[0])  # b

# zip with list()
a = [1, 2, 3]
b = ["x", "y", "z"]
zipped = list(zip(a, b))
print(zipped[0][0])  # 1
print(zipped[0][1])  # x
print(len(zipped))   # 3
