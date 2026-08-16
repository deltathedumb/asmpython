# expect:
# 1
# None
# 99
# 9.5
# None

d = {"x": 1, "y": 2}
print(d.get("x"))
print(d.get("z"))
print(d.get("z", 99))

f = {"a": 9.5}
print(f.get("a"))
print(f.get("b"))
