# probes: two equal dicts are not the same object
# expect:
# False
# True
a = {"k": 1}
b = {"k": 1}
print(a is b)
print(a == b)
