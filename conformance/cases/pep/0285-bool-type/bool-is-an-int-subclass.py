# tier: spec
# ref: library/stdtypes.html#boolean-values
# expect:
# 2 3
# True True
# 1 True False
# True False
# 1
# 2
print(True + True, True * 3)
print(isinstance(True, int), issubclass(bool, int))
print(int(True), bool(2), bool(0))
print(str(True), repr(False))
print([True, False].count(True))
print(sum([True, True, False]))
