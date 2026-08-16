# expect:
# 1
# False
# 0
# True
# 0
# 10
# 1
# 2
# 99
# 2

# dict.pop, clear, copy, setdefault.

d = {"a": 1, "b": 2}
v = d.pop("a")
print(v)              # 1
print("a" in d)       # False
print(d.pop("x", 0))  # 0  (default for missing key)
print("b" in d)       # True

d2 = {"x": 10}
e = d2.copy()
d2.clear()
print(len(d2))   # 0
print(e["x"])    # 10

f = {"a": 1}
print(f.setdefault("a", 99))  # 1   (already present, unchanged)
print(f.setdefault("b", 2))   # 2   (inserted)
print(f.setdefault("c", 99))  # 99  (inserted)
print(f["b"])                 # 2
