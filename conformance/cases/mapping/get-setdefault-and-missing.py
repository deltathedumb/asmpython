# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# 1 None 0
# 2
# 2
# [('a', 1), ('b', 2)]
# KeyError
d = {"a": 1}
print(d.get("a"), d.get("b"), d.get("b", 0))
print(d.setdefault("b", 2))
print(d.setdefault("b", 99))
print(sorted(d.items()))
try:
    d["zz"]
except KeyError:
    print("KeyError")
