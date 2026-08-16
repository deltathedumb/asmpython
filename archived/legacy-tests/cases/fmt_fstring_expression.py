# probes: an f-string evaluates a full expression
# expect:
# 7
# 13
a = 3
b = 4
print(f"{a + b}")
print(f"{a * b + 1}")
