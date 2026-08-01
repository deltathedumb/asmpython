# tier: spec
# ref: library/stdtypes.html#common-sequence-operations
# expect:
# []
# [1, 2]
# []
#
# 3
xs = [1, 2, 3]
print(xs[5:10])
print(xs[-10:2])
print(xs[2:1])
print("abc"[5:])
print(xs[::-1][0])
