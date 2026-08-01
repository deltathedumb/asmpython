# tier: spec
# ref: library/stdtypes.html#dict-views
# expect:
# ['a', 'b']
# [1, 2]
# [('a', 1), ('b', 2)]
# True
# True
# 2
# ['a']
d = {"a": 1, "b": 2}
print(sorted(d.keys()))
print(sorted(d.values()))
print(sorted(d.items()))
print(("a", 1) in d.items())
print("a" in d.keys())
print(len(d.items()))
print(sorted(d.keys() & {"a", "c"}))
