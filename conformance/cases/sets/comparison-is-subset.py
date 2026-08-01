# tier: spec
# ref: library/stdtypes.html#set
# expect:
# True False
# True False
# True True
# True
a = {1, 2}
b = {1, 2, 3}
print(a < b, b < a)
print(a <= a, a < a)
print(a.issubset(b), b.issuperset(a))
print({1, 2} == {2, 1})
