# tier: spec
# ref: library/functions.html#float
# expect:
# 1.5
# 2000.0
# inf
# -inf
# inf -inf
# ValueError
print(float("1.5"))
print(float("  2e3 "))
print(float("1e400"))
print(float("-1e400"))
print(float("inf"), float("-inf"))
try:
    float("abc")
except ValueError:
    print("ValueError")
