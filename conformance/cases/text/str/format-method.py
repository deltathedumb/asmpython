# tier: spec
# ref: library/stdtypes.html#str.format
# expect:
# 1 a
# b a
# 3
#     x|
# 03.14
print("{} {}".format(1, "a"))
print("{1} {0}".format("a", "b"))
print("{k}".format(k=3))
print("{:>5}|".format("x"))
print("{:05.2f}".format(3.14159))
