# probes: dict(a) produces an independent dict
# expect:
# 1
# 2
a = {"k": 1}
b = dict(a)
b["new"] = 2
print(len(a))
print(len(b))
