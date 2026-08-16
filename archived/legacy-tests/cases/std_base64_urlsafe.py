# probes: urlsafe_b64encode uses the URL alphabet
# expect:
# b'-_8='
import base64

print(base64.urlsafe_b64encode(b"\xfb\xff"))
