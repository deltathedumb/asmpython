# probes: b64encode/b64decode round-trip
# expect:
# b'aGVsbG8='
# b'hello'
import base64

encoded = base64.b64encode(b"hello")
print(encoded)
print(base64.b64decode(encoded))
