# tier: spec
# ref: library/stdtypes.html#dict
# expect:
# RuntimeError
# 3
d = {"a": 1, "b": 2}
try:
    for k in d:
        d["c"] = 3
except RuntimeError:
    print("RuntimeError")
print(len(d))
