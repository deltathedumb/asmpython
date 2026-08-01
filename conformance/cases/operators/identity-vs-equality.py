# tier: spec
# ref: reference/expressions.html#is-not
# expect:
# True False True
# True
# True True
# True False
a = [1]
b = [1]
print(a == b, a is b, a is not b)
c = a
print(c is a)
print(None is None, None == None)
print(True is True, 1 is True)
