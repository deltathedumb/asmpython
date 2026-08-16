# expect:
# 8
# 255
# 127
# 65535
# 65534
# 1

from struct import calcsize, pack, unpack, Struct

print(calcsize("<Q"))

packed = pack("<BB", 255, 127)
vals = unpack("<BB", packed)
print(vals[0])
print(vals[1])

packed2 = pack("<HH", 65535, 65534)
vals2 = unpack("<HH", packed2)
print(vals2[0])
print(vals2[1])

s = Struct("<B")
print(s.size)
