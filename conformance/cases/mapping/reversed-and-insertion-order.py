# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# ['a', 'b', 'c']
# ['c', 'b', 'a']
# ['a', 'c', 'b']
# ('b', 9)
d = {"a": 1, "b": 2, "c": 3}
print(list(d))
print(list(reversed(d)))
del d["b"]
d["b"] = 9
print(list(d))
print(list(d.items())[-1])
