# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# [('a', 1), ('b', 2), ('c', 3)]
# 1
# default
# ['b', 'c']
# True
d = {"a": 1}
d.update({"b": 2})
d.update(c=3)
print(sorted(d.items()))
print(d.pop("a"))
print(d.pop("zz", "default"))
print(sorted(d))
print(d.popitem() in (("c", 3), ("b", 2)))
