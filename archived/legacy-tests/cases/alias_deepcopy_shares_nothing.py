# probes: copy.deepcopy shares nothing
# expect:
# [1]
# [1, 2]
import copy

a = {"in": [1]}
b = copy.deepcopy(a)
b["in"].append(2)
print(a["in"])
print(b["in"])
