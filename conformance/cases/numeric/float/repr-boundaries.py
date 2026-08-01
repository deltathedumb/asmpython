# tier: spec
# ref: library/stdtypes.html#float
# expect:
# 1e+16
# 1e+17
# 0.0001
# 1e-05
# 1234567890123456.0
# 0.5
# 100.0
# 0.3333333333333333
# 9007199254740992.0
for v in (1e16, 1e17, 1e-4, 1e-5, 1234567890123456.0, 0.5, 100.0):
    print(repr(v))
print(repr(1/3))
print(repr(2.0 ** 53))
