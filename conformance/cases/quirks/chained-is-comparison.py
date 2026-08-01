# tier: spec
# ref: reference/expressions.html#comparisons
# expect:
# True False
# True
# False
a = []
b = []
print(a == b, a is b)
print(a is a is a)
print(1 in [1] == True)
