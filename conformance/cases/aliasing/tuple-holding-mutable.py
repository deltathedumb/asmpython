# tier: spec
# ref: library/stdtypes.html#tuple
# expect:
# ([1, 2], 'x')
# TypeError
# [1, 2]
inner = [1]
t = (inner, "x")
inner.append(2)
print(t)
try:
    t[0] = []
except TypeError:
    print("TypeError")
print(t[0])
