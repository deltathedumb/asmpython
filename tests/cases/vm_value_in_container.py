# probes: membership uses value equality
# expect:
# True
# False
# True
# False
names = ["ada", "bob"]
print("ada" in names)
print("carol" in names)
d = {"k": 1}
print("k" in d)
print("z" in d)
