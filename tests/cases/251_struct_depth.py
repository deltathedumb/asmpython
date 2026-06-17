# expect:
# 1
# 255
# 1000
# 8
# 4
# 2

import struct

packed = struct.pack("B", 255)
print(packed[0] - 254)
print(packed[0])

packed2 = struct.pack("<H", 1000)
v = packed2[0] + packed2[1] * 256
print(v)

print(struct.calcsize("Q"))
print(struct.calcsize("I"))
print(struct.calcsize("H"))
