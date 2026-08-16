# probes: sys.maxsize is the 64-bit signed maximum
# expect:
# 9223372036854775807
# True
import sys

print(sys.maxsize)
print(sys.maxsize == 2 ** 63 - 1)
