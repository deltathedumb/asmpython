# tier: impl
# expect:
# False
# False
# True
x = [1]
y = [1]
print(id(x) == id(y))
print(x is y)
print(x == y)
