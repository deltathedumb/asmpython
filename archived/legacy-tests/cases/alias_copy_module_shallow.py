# probes: copy.copy is one level deep
# expect:
# [1, 2]
# 1
import copy

inner = [1]
a = {"in": inner}
b = copy.copy(a)
b["in"].append(2)
b["new"] = 1
print(a["in"])
print(len(a))
