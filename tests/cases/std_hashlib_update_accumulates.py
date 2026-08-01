# probes: hash.update appends to the running digest
# expect:
# True
import hashlib

h = hashlib.sha1()
h.update(b"ab")
h.update(b"c")
print(h.hexdigest() == hashlib.sha1(b"abc").hexdigest())
