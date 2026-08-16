# expect:
# 4
# 8
# 255
# 1000
# -1

import struct

print(struct.calcsize("I"))
print(struct.calcsize("Q"))

packed: list = struct.pack("B", 255)
print(packed[0])

packed2: list = struct.pack("H", 1000)
result2: list = struct.unpack("H", packed2)
print(result2[0])

packed3: list = struct.pack("b", -1)
result3: list = struct.unpack("b", packed3)
print(result3[0])
