# probes: a dict containing itself renders as {...}
# expect:
# 2
# True
# {'n': 1, 'self': {...}}
d = {"n": 1}
d["self"] = d
print(len(d))
print(d["self"] is d)
print(d)
