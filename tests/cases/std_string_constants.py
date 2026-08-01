# probes: string exposes the ASCII constants
# expect:
# abcdefghijklmnopqrstuvwxyz
# 0123456789
# 0123456789abcdefABCDEF
import string

print(string.ascii_lowercase)
print(string.digits)
print(string.hexdigits)
