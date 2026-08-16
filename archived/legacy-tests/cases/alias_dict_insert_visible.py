# probes: inserting through a dict alias is visible
# expect:
# 2
# 2
a = {"k": 1}
b = a
b["new"] = 2
print(len(a))
print(a["new"])
