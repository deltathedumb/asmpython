# probes: unhexlify inverts hexlify
# expect:
# b'ab'
import binascii

print(binascii.unhexlify(binascii.hexlify(b"ab")))
