# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# 1
# float
# [1]
d = {}
d[1] = "int"
d[True] = "bool"
d[1.0] = "float"
print(len(d))
print(d[1])
print(list(d))
