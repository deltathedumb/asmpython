# probes: round(x) is int; round(x, n) is float
# expect:
# 3
# 2
# 2.5
# 3.14
# 4
print(round(2.6))
print(round(2.4))
print(round(2.55, 1))
print(round(3.14159, 2))
r = round(2.6)
print(r + 1)
