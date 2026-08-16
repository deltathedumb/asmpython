# probes: quopri decode inverts encode
# expect:
# True
import quopri

payload = b"caf\xc3\xa9 = tasty"
print(quopri.decodestring(quopri.encodestring(payload)) == payload)
