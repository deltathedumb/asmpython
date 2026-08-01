# probes: a dict iterates in insertion order
# expect:
# ['z', 'a', 'm']
# [1, 2, 3]
d = {}
d["z"] = 1
d["a"] = 2
d["m"] = 3
print(list(d.keys()))
print(list(d.values()))
