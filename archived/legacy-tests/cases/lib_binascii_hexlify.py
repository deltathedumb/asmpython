# expect:
# b'4142'
import binascii
print(binascii.hexlify(b'AB'))
# asmpython (beta/3.14.0) MISMATCH: prints '0\n' (wrong).
