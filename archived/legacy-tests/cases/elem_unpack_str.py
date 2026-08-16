# probes: tuple unpacking reads both elements (str elements)
# expect:
# aa
# bb
pair = ("aa", "bb")
a, b = pair
print(a)
print(b)
