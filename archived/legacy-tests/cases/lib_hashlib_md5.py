# expect:
# 5d41402abc4b2a76b9719d911017c592
import hashlib
print(hashlib.md5(b'hello').hexdigest())
# asmpython (beta/3.14.0) rejects at compile: [E021] md5() takes 0 argument(s), got 1
