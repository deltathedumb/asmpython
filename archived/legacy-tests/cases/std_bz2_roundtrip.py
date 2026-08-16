# probes: bz2 compress/decompress round-trips
# expect:
# True
import bz2

data = b"repeat repeat repeat"
print(bz2.decompress(bz2.compress(data)) == data)
