# expect:
# {'a': 1, 'b': 2, 'c': 3}
# a
# b
# c
# a 1
# b 2
# c 3
# {'a': 2, 'b': 4, 'c': 6}
# {'a': 1, 'b': 2, 'c': 3, 'd': 4}
# {'a': 1, 'c': 3, 'd': 4}
# a
# c
# d
# 1
# {'c': 3, 'd': 4}
# {'z': 3, 'x': 1, 'y': 2, 'w': 4}

# Dicts preserve insertion order (CPython 3.7+ guarantee): printing,
# iteration, .items()/.keys()/.values(), comprehensions, and growth/removal
# all walk entries in the order keys were first inserted.
d = {"a": 1, "b": 2, "c": 3}
print(d)

for k in d:
    print(k)

for k, v in d.items():
    print(k, v)

print({k: v * 2 for k, v in d.items()})

# New key is appended at the end, regardless of hash bucket.
d["d"] = 4
print(d)

# Removing a key closes the gap; remaining keys keep their relative order.
del d["b"]
print(d)
for k in d:
    print(k)

x = d.pop("a")
print(x)
print(d)

# Dict-union/unpack: new keys from the right-hand dict are appended in
# *its* insertion order.
d2 = {"x": 1, "y": 2}
d3 = {"z": 3, **d2, "w": 4}
print(d3)
