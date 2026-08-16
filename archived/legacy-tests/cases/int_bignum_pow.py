# expect:
# 18446744073709551616
print(2 ** 64)
# no arbitrary-precision ints; asmpython (beta/3.14.0) overflows 2**64 to 0.
