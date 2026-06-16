# probe30: string formatting patterns
# int with width spec in f-string
n = 42
print(f"{n:5d}")      # "   42" (right-aligned in width 5)
print(f"{n:05d}")     # "00042"
print(f"{n:<5d}")     # "42   " (left-aligned)
print(f"{n:b}")       # "101010"
print(f"{n:x}")       # "2a"
print(f"{n:#010b}")   # "0b00101010"

# float formatting
pi = 3.14159
print(f"{pi:.2f}")    # 3.14
print(f"{pi:.4f}")    # 3.1416
