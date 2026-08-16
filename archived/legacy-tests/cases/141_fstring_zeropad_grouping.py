# expect:
# [000,001,234,567]
# [0,000,001,234,567]
# [-000,001,234,567]
# [-0,000,001,234,567]
# [0,001]
# [00,001]
# [0,000]
# [0,000,012]
# [000,012]
# [000,100]
# [-0,001]
# [0,001,234,567]
# [0,001,234,567]
# [0_001_234]
# [0,000,000,003.14]
# [0,000,001,234,567.89]
# [-0,000,001,234,567.89]
n = 1234567
neg = -1234567
small = 1
twelve = 12
hundred = 100
zero = 0
f = 3.14159
bigf = 1234567.89
negf = -1234567.89

# Zero-pad width + grouping (`f"{n:015,}"`): CPython zero-pads the integer
# part so the *grouped* result reaches the requested width, separator-aware.
print(f"[{n:015,}]")
print(f"[{n:016,}]")
print(f"[{neg:016,}]")
print(f"[{neg:017,}]")
print(f"[{small:05,}]")
print(f"[{small:06,}]")
print(f"[{zero:05,}]")
print(f"[{twelve:08,}]")
print(f"[{twelve:07,}]")
print(f"[{hundred:07,}]")
print(f"[{-1:06,}]")
print(f"[{n:012,}]")
print(f"[{n:013,}]")
print(f"[{1234:08_}]")
print(f"[{f:015,.2f}]")
print(f"[{bigf:020,.2f}]")
print(f"[{negf:020,.2f}]")
