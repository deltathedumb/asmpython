# probes: bytearray is mutable
# expect:
# 3
# 122
# b'zbc'
ba = bytearray(b"abc")
ba[0] = 122
print(len(ba))
print(ba[0])
print(bytes(ba))
