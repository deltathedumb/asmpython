# expect:
# 1
# 2
# 1
# 4

d: dict[str, dict[str, int]] = {}
d["a"] = {"x": 1, "y": 2}
d["b"] = {"x": 3, "y": 4}
v = d["a"]
print(v["x"])
print(v["y"])
print(d["a"]["x"])
print(d["b"]["y"])
