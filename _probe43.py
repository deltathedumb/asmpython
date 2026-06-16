# probe43: dict operations
d: dict[str, int] = {}
d["a"] = 1
d["b"] = 2
d["c"] = 3

# iteration over items
for k, v in d.items():
    print(k, v)

# dict.update
e: dict[str, int] = {"x": 10}
e.update({"y": 20, "z": 30})
print(e["x"])  # 10
print(e["y"])  # 20

# dict.pop
val = e.pop("y")
print(val)     # 20
print("y" in e)  # False

# dict.setdefault
f: dict[str, int] = {"a": 1}
f.setdefault("a", 99)  # existing key - should not change
f.setdefault("b", 2)   # new key
print(f["a"])  # 1
print(f["b"])  # 2

# dict comprehension
g = {k: v * 2 for k, v in d.items()}
print(g["a"])  # 2
print(g["b"])  # 4
