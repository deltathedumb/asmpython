# expect:
# True
import gzip
data = b'hello world'
c = gzip.compress(data)
print(gzip.decompress(c) == data)
# asmpython (beta/3.14.0) MISMATCH: prints 'False\n' (wrong).
