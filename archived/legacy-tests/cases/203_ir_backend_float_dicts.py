# expect:
# 1.25
# 2.5
# 3.75
d = {"rate": 1.25}
print(d["rate"])
d["rate"] = 2.5
print(d.get("rate"))
print(d.get("missing", 3.75))
