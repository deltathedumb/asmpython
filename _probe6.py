# probe: more patterns

# 1. list.index()
xs = [10, 20, 30, 40]
print(xs.index(30))
print(xs.index(10))

# 2. list.remove()
ys = [1, 2, 3, 2, 4]
ys.remove(2)
print(ys[0])
print(ys[1])
print(ys[2])
print(len(ys))

# 3. list.sort() in-place
zs = [3, 1, 4, 1, 5]
zs.sort()
print(zs[0])
print(zs[4])

# 4. dict.get() with default
d: dict[str, int] = {"a": 1, "b": 2}
print(d.get("a", 0))
print(d.get("c", 99))

# 5. dict.setdefault()
d2: dict[str, int] = {}
d2.setdefault("x", 10)
print(d2["x"])
