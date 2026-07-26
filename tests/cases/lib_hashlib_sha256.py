# expect:
# 2d711642
import hashlib
print(hashlib.sha256(b'x').hexdigest()[:8])
# asmpython (beta/3.14.0) rejects at compile: [E021] sha256() takes 0 argument(s), got 1
