# expect:
# 64
import hmac, hashlib
h = hmac.new(b'key', b'msg', hashlib.sha256)
print(len(h.hexdigest()))
# asmpython (beta/3.14.0) rejects at compile: [P002] expected NEWLINE, got OP ','
