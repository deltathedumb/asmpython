# tier: spec
# ref: library/functions.html#int
# expect:
# 12
# -3
# 3
# 1000
# ValueError
# ValueError
print(int("  12  "))
print(int("-3"))
print(int("+3"))
print(int("1_000"))
try:
    int("12.5")
except ValueError:
    print("ValueError")
try:
    int("")
except ValueError:
    print("ValueError")
