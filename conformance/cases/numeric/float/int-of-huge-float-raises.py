# tier: spec
# ref: library/functions.html#int
# expect:
# 1000000000000000
# -1000000000000000
# OverflowError
# ValueError
print(int(1e15))
print(int(-1e15))
try:
    int(float("inf"))
except OverflowError:
    print("OverflowError")
try:
    int(float("nan"))
except ValueError:
    print("ValueError")
