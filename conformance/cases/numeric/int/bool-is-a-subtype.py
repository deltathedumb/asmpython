# tier: spec
# ref: library/stdtypes.html#boolean-values
# expect:
# 2
# True
# 2
# 1
print(True + 1)
print(isinstance(True, int))
print(sum([True, True, False]))
print(int(True))
