# expect:
# b'aGVsbG8='
import base64
print(base64.b64encode(b'hello'))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
