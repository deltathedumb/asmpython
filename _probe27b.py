# dict operations (without dict(pairs))
d = {"a": 1, "b": 2, "c": 3}
print(len(d))   # 3

d.update({"d": 4, "e": 5})
print(len(d))   # 5

v = d.pop("a")
print(v)        # 1
print(len(d))   # 4

print("b" in d)    # True
print("a" in d)    # False
