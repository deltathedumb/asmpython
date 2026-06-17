# expect:
# [101010]
# [0b101010]
# [00101010]
# [0b00101010]
# [-101]
# [-0b101]
# [-0000101]
# [-0b0000101]
# [0]
# [0b0]
# [0000]
# [****101010]
# [101010****]
# [***101010***]
# [******-101]
n = 42
neg = -5
zero = 0

# Binary format spec `b` (and `#b` for the "0b" prefix), with optional
# zero-pad width -- prefix and sign are counted toward the width.
print(f"[{n:b}]")
print(f"[{n:#b}]")
print(f"[{n:08b}]")
print(f"[{n:#010b}]")
print(f"[{neg:b}]")
print(f"[{neg:#b}]")
print(f"[{neg:08b}]")
print(f"[{neg:#010b}]")
print(f"[{zero:b}]")
print(f"[{zero:#b}]")
print(f"[{zero:04b}]")

# Explicit alignment overrides zero-pad: digits are unpadded, then
# justified to width with the given fill char.
print(f"[{n:*>10b}]")
print(f"[{n:*<10b}]")
print(f"[{n:*^12b}]")
print(f"[{neg:*>10b}]")
