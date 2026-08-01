# tier: spec
# ref: library/functions.html#int
# expect:
# 31 31
# 5 15
# 31
# ValueError
# -42
print(int("0x1f", 16), int("1f", 16))
print(int("0b101", 2), int("0o17", 8))
print(int("0x1f", 0))
try:
    int("0x1f")
except ValueError:
    print("ValueError")
print(int("  -42  "))
