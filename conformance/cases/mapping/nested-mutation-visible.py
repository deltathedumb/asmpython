# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# {'k': {'inner': 2}}
# [('inner', 2), ('new', 3)]
d = {"k": {"inner": 1}}
d["k"]["inner"] = 2
print(d)
inner = d["k"]
inner["new"] = 3
print(sorted(d["k"].items()))
