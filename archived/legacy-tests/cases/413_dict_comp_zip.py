# expect:
# 1
# 2
# 3
# None

keys = ["a", "b", "c"]
vals = [1, 2, 3]
d = {k: v for k, v in zip(keys, vals)}
print(d.get("a"))
print(d.get("b"))
print(d.get("c"))
print(d.get("z"))
