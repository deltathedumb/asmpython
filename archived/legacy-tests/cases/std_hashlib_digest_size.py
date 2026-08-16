# probes: a hash object reports its digest_size
# expect:
# 32
import hashlib

print(hashlib.sha256().digest_size)
