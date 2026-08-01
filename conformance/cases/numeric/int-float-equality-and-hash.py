# tier: spec
# ref: library/stdtypes.html#numeric-types-int-float-complex
# expect:
# True True
# True
# False
# True
print(1 == 1.0, hash(1) == hash(1.0))
print(2 ** 53 == float(2 ** 53))
print((2 ** 53 + 1) == float(2 ** 53 + 1))
print(int(3.0) == 3)
