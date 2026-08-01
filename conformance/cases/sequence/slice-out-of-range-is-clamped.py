# tier: spec
# ref: library/stdtypes.html#common-sequence-operations
# expect:
# []
# [1, 2, 3]
# [1, 2, 3]
# IndexError
xs = [1, 2, 3]
print(xs[10:])
print(xs[:100])
print(xs[-100:])
try:
    xs[10]
except IndexError:
    print("IndexError")
