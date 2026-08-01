# tier: spec
# ref: library/stdtypes.html#bytes.hex
# expect:
# 01ff10
# 01:ff:10
# b'\x01\xff\x10'
# [1, 255, 16]
# b'\x00\x00\x00'
b = bytes([1, 255, 16])
print(b.hex())
print(b.hex(":"))
print(bytes.fromhex("01ff10"))
print(list(b))
print(bytes(3))
