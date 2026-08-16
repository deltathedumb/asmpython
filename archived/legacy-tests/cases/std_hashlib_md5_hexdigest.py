# probes: hashlib.md5 accepts data in the constructor
# expect:
# 900150983cd24fb0d6963f7d28e17f72
import hashlib

print(hashlib.md5(b"abc").hexdigest())
