# probe125: complex dict operations and unpacking
d1 = {"a": 1, "b": 2, "c": 3}
d2 = {"b": 20, "d": 4}

# Dict merge with | operator (Python 3.9+)
merged = d1 | d2
print(merged["a"])   # 1
print(merged["b"])   # 20 (d2 wins)
print(merged["c"])   # 3
print(merged["d"])   # 4
print(len(merged))   # 4

# Dict merge with |=
d3 = {"x": 1}
d3 |= {"y": 2, "x": 99}
print(d3["x"])   # 99
print(d3["y"])   # 2

# Dict from comprehension with complex key
squares = {str(i): i * i for i in range(5)}
print(squares["0"])   # 0
print(squares["4"])   # 16

# Nested dict update
config = {"server": {"host": "localhost", "port": 8080}}
config["server"]["port"] = 9090
print(config["server"]["port"])   # 9090

# Dict with list values (group-by pattern)
groups: dict = {}
for item in ["apple", "banana", "avocado", "blueberry", "cherry"]:
    key = item[0]
    if key not in groups:
        groups[key] = []
    groups[key].append(item)

print(len(groups["a"]))   # 2
print(len(groups["b"]))   # 2
print(len(groups["c"]))   # 1
print(groups["a"][0])     # apple
print(groups["a"][1])     # avocado
