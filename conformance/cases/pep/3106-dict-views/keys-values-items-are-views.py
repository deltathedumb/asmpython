# tier: spec
# ref: library/stdtypes.html#dict-views
# expect:
# dict_keys
# ['a', 'b', 'c']
# 3
# ['a', 'b', 'c', 'z']
# ('a', 1)
# dict_values
d = {"a": 1, "b": 2}
k = d.keys()
print(type(k).__name__)
d["c"] = 3
print(sorted(k))
print(len(k))
print(sorted(d.keys() | {"z"}))
print(sorted(d.items())[0])
print(type(d.values()).__name__)
