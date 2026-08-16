# expect:
# aaf4c61d
import hashlib
h = hashlib.sha1()
h.update(b'hello')
print(h.hexdigest()[:8])
# asmpython (beta/3.14.0) rejects at compile: [E113] int has no method 'update'
