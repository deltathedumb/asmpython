# probes: tuple unpacking reads both elements (float elements)
# expect:
# 1.5
# 2.5
pair = (1.5, 2.5)
a, b = pair
print(a)
print(b)
