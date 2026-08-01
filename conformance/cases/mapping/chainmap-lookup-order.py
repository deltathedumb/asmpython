# tier: spec
# ref: library/collections.html#collections.ChainMap
# expect:
# 1 2 30
# [('x', 1), ('y', 99)] [('y', 20), ('z', 30)]
# ['x', 'y', 'z']
# 2
from collections import ChainMap

a = {"x": 1, "y": 2}
b = {"y": 20, "z": 30}
cm = ChainMap(a, b)
print(cm["x"], cm["y"], cm["z"])
cm["y"] = 99
print(sorted(a.items()), sorted(b.items()))
print(sorted(cm.keys()))
print(len(cm.maps))
