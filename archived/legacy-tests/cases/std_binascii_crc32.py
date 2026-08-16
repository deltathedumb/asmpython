# probes: binascii.crc32 matches CPython's checksum
# expect:
# 907060870
import binascii

print(binascii.crc32(b"hello"))
