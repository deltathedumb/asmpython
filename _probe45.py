# probe45: more string formatting and conversions
# bin/hex/oct
print(bin(10))    # 0b1010
print(hex(255))   # 0xff
print(oct(8))     # 0o10

# int with base
print(int("0b1010", 0))   # 10
print(int("0xff", 0))     # 255
print(int("ff", 16))      # 255
print(int("1010", 2))     # 10

# str.format() basic
msg = "Hello {}!".format("world")
print(msg)   # Hello world!

# multi-value format
s = "{} + {} = {}".format(1, 2, 3)
print(s)     # 1 + 2 = 3
