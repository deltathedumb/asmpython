# probe27: dict operations
d = {"a": 1, "b": 2, "c": 3}

# keys(), values(), items() iteration
for k in d.keys():
    pass  # just iterate

# len of dict
print(len(d))   # 3

# dict.update
d.update({"d": 4, "e": 5})
print(len(d))   # 5

# dict.pop
v = d.pop("a")
print(v)        # 1
print(len(d))   # 4

# "in" operator
print("b" in d)    # True
print("a" in d)    # False (was popped)

# dict from pairs
pairs = [("x", 10), ("y", 20)]
d2 = dict(pairs)
print(d2.get("x"))  # 10
print(d2.get("y"))  # 20
