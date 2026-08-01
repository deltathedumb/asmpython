# tier: spec
# ref: reference/lexical_analysis.html#integer-literals
# expect:
# 10 15 255
# 255 10 15
# 0b1010 0o17 0xff
print(0b1010, 0o17, 0xff)
print(int("ff", 16), int("1010", 2), int("17", 8))
print(bin(10), oct(15), hex(255))
