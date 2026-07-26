# expect:
# (1, 2)
import struct
packed = struct.pack('<hh', 1, 2)
print(struct.unpack('<hh', packed))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
