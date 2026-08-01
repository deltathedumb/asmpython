# tier: cpython
# ref: library/stdtypes.html#typesnumeric
# expect:
# 0.30000000000000004
# False
# True
# 3.3000000000000003
# 5.551115123125783e-17
print(0.1 + 0.2)
print(0.1 + 0.2 == 0.3)
print(round(0.1 + 0.2, 10) == 0.3)
print(1.1 * 3)
print(0.1 + 0.2 - 0.3)
