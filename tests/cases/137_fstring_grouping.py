# expect:
# [1,234,567]
# [-1,234,567]
# [123]
# [1_234_567]
# [1,234,567]
# [1,234,567.89]
# [-1,234,567.89]
# [1,234.50]
# [      1,234,567]
# [0]
n = 1234567
neg = -1234567
small = 123
f = 1234567.891
nf = -1234567.891
sf = 1234.5
zero = 0

# Thousands-separator grouping options `,` and `_` (PEP 378/515): inserted
# every 3 digits in the integer part, combinable with the `d` type, float
# precision, and alignment/width.
print(f"[{n:,}]")
print(f"[{neg:,}]")
print(f"[{small:,}]")
print(f"[{n:_}]")
print(f"[{n:,d}]")
print(f"[{f:,.2f}]")
print(f"[{nf:,.2f}]")
print(f"[{sf:,.2f}]")
print(f"[{n:>15,}]")
print(f"[{zero:,}]")
