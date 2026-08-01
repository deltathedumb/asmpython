# tier: impl
# ref: library/stdtypes.html#numeric-types-int-float-complex
# expect:
# True True
# True True
a = 256
b = 256
print(a is b, a == b)
c = 257
d = 257
print(c is d, c == d)
