# probes: tuple unpacking reads both elements (mixed elements)
# expect:
# 1
# two
pair = (1, "two")
a, b = pair
print(a)
print(b)
