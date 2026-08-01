# probes: tuple unpacking reads both elements (int elements)
# expect:
# 10
# 20
pair = (10, 20)
a, b = pair
print(a)
print(b)
