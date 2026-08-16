# probes: quopri encodes bytes as quoted-printable
# expect:
# b'a=3Db'
import quopri

print(quopri.encodestring(b"a=b"))
