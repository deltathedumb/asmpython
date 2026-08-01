# probes: dict.copy produces an independent dict
# expect:
# 1
# 9
a = {"k": 1}
b = a.copy()
b["k"] = 9
print(a["k"])
print(b["k"])
