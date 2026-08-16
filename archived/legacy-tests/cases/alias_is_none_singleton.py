# probes: None is a singleton
# expect:
# True
# True
# True
a = None
b = None
print(a is b)
print(a is None)
print(a == None)
