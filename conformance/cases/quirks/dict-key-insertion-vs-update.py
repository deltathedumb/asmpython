# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# ['a'] 2
# ['a'] 3
# ['b', 'a']
d = {"a": 1}
d["a"] = 2
print(list(d), d["a"])
d.update(a=3)
print(list(d), d["a"])
d2 = {"b": 1, "a": 1}
d2["a"] = 9
print(list(d2))
