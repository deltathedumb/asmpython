# probes: struct round-trips a packed record
# expect:
# 6
# (7, 3)
import struct

packed = struct.pack("<ih", 7, 3)
print(len(packed))
print(struct.unpack("<ih", packed))
