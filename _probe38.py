# probe38: bitwise ops, shifts, bit manipulation
x = 0b1010   # 10
y = 0b1100   # 12

print(x & y)   # 8  (0b1000)
print(x | y)   # 14 (0b1110)
print(x ^ y)   # 6  (0b0110)
print(~x)      # -11
print(x << 2)  # 40
print(x >> 1)  # 5

# Combined operations
n = 255
print(n & 0xF)     # 15
print(n >> 4)      # 15
print((n >> 4) & 0xF)  # 15

# Power of 2 checks
for i in range(8):
    if (1 << i) & x:
        print(i, end=" ")
print()

# Byte extraction
val = 0xABCD
hi = (val >> 8) & 0xFF
lo = val & 0xFF
print(hi)   # 0xAB = 171
print(lo)   # 0xCD = 205
