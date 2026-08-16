# probes: zlib compress/decompress round-trips
# expect:
# True
import zlib

data = b"hello hello hello hello"
print(zlib.decompress(zlib.compress(data)) == data)
