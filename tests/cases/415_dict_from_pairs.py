# expect:
# 10
# 20
# None

pairs = [("x", 10), ("y", 20)]
d = dict(pairs)
print(d.get("x"))
print(d.get("y"))
print(d.get("z"))
