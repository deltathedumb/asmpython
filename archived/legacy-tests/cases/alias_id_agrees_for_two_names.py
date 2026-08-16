# probes: two names for one object share an id
# expect:
# True
a = [1]
b = a
print(id(a) == id(b))
