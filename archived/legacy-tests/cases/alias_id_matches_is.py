# probes: id() equality agrees with `is`
# expect:
# True
# True
a = {"k": 1}
b = a
c = {"k": 1}
print((a is b) == (id(a) == id(b)))
print((a is c) == (id(a) == id(c)))
