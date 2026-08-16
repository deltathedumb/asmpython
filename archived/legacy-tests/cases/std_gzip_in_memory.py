# probes: gzip round-trips through a byte stream
# expect:
# True
import gzip

data = b"repeat repeat repeat"
print(gzip.decompress(gzip.compress(data)) == data)
