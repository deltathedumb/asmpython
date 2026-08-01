# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# 1
# bool
d = {1: "int", 1.0: "float", True: "bool"}
print(len(d))
print(d[1])
