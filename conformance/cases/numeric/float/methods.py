# tier: spec
# ref: library/stdtypes.html#float
# expect:
# False True
# (5, 2)
# 0x1.4000000000000p+1
# 2.5
# False 1
f = 2.5
print(f.is_integer(), (2.0).is_integer())
print(f.as_integer_ratio())
print(f.hex())
print(float.fromhex("0x1.4p+1"))
print((0.0).__bool__(), (1.5).__trunc__())
