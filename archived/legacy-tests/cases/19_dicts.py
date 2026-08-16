# expect:
# 2
# 1
# 100
# 99
# 5
# 0
# 1
# default
d = {"a": 1, "b": 2}
print(len(d))
print(d["a"])

d["a"] = 100
print(d["a"])

d["c"] = 99
print(d["c"])
print(len(d) + 2)

print(d.contains("zzz"))
print(d.contains("a"))

# get with default
missing_default = d.get("nope", 7)
if missing_default == 7:
    print("default")
else:
    print("nondefault")
