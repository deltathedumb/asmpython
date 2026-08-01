# tier: spec
# ref: library/stdtypes.html#dict-views
# expect:
# ['a']
# ['a', 'b']
# 2
d = {"a": 1}
ks = d.keys()
print(sorted(ks))
d["b"] = 2
print(sorted(ks))
print(len(ks))
