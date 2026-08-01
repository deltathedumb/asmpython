# tier: impl
# ref: library/functions.html#id
# expect:
# True
# True
# True
# True
a = [1]
print(id(a) == id(a))
b = a
print(id(a) == id(b))
print(id(a) != id([1]))
print(isinstance(id(a), int))
