# probes: update() through an alias is visible
# expect:
# 9
# 2
a = {"k": 1}
b = a
b.update({"k": 9, "j": 2})
print(a["k"])
print(len(a))
