# probes: hmac.new(key, msg, digestmod) works
# expect:
# 6e9ef29b75fffc5b7abae527d58fdadb2fe42e7219011976917343065f58ed4a
import hashlib
import hmac

print(hmac.new(b"key", b"message", hashlib.sha256).hexdigest())
