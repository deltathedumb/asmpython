# tier: spec
# ref: library/stdtypes.html#numeric-types-int-float-complex
# expect:
# True
# int
# 1000000000000000000000000000001
# True
# True
small = 1
big = 10 ** 30
print(type(small) is type(big))
print(type(big).__name__)
print(big + 1)
print(isinstance(big, int))
print((2 ** 31) * (2 ** 31) == 2 ** 62)
