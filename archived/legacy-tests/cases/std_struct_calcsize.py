# probes: struct.calcsize reports the format width
# expect:
# 4
# 8
import struct

print(struct.calcsize("<i"))
print(struct.calcsize("<q"))
