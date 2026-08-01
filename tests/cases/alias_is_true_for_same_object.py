# probes: `is` is True for two names of one object
# expect:
# True
# True
a = [1]
b = a
print(a is b)
print(a == b)
