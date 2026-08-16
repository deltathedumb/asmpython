# expect:
# True
import zlib
print(zlib.crc32(b'hello') > 0)
# asmpython (beta/3.14.0) MISMATCH: prints 'False\n' (wrong).
