# tier: spec
# ref: library/stdtypes.html#int
# expect:
# 8 8
# b'\x00\xff' b'\xff\x00'
# 256
# 5 -5
# (10, 1)
# 7 7 0
n = 255
print(n.bit_length(), n.bit_count())
print(n.to_bytes(2, "big"), n.to_bytes(2, "little"))
print(int.from_bytes(b"\x01\x00", "big"))
print((-5).__abs__(), (5).__neg__())
print((10).as_integer_ratio())
print((7).conjugate(), (7).real, (7).imag)
