# probes: deleting through a dict alias is visible
# expect:
# 1
# False
a = {"k": 1, "j": 2}
b = a
del b["k"]
print(len(a))
print("k" in a)
