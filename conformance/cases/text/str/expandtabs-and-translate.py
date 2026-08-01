# tier: spec
# ref: library/stdtypes.html#str.expandtabs
# expect:
# 'a       b'
# 'a   b'
# xxyy
# ac
print(repr("a\tb".expandtabs()))
print(repr("a\tb".expandtabs(4)))
table = str.maketrans("ab", "xy")
print("aabb".translate(table))
print("abc".translate(str.maketrans("", "", "b")))
