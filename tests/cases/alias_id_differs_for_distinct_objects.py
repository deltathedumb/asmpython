# probes: distinct objects have distinct ids
# expect:
# False
# True
a = [1]
b = [1]
print(id(a) == id(b))
print(a == b)
