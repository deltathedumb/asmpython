# probe76: bit operations, integer methods, abs, divmod, pow
# Bitwise
a = 0b1010   # 10
b = 0b1100   # 12
print(a & b)   # 8
print(a | b)   # 14
print(a ^ b)   # 6
print(~a)      # -11
print(a << 1)  # 20
print(a >> 1)  # 5

# abs
print(abs(-5))    # 5
print(abs(5))     # 5
print(abs(-100))  # 100

# divmod
q, r = divmod(17, 5)
print(q)   # 3
print(r)   # 2

q2, r2 = divmod(100, 7)
print(q2)   # 14
print(r2)   # 2

# pow
print(pow(2, 10))    # 1024
print(pow(3, 3))     # 27
print(pow(2, 0))     # 1

# hex/oct/bin conversion
n = 255
print(hex(n))     # 0xff
print(oct(n))     # 0o377
print(bin(n))     # 0b11111111
print(int("ff", 16))   # 255
print(int("377", 8))   # 255
print(int("11111111", 2))  # 255
