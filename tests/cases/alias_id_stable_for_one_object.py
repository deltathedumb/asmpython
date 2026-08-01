# probes: id() is stable for a live object
# expect:
# True
a = [1]
first = id(a)
a.append(2)
print(id(a) == first)
