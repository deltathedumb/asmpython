# tier: spec
# ref: library/stdtypes.html#str.encode
# expect:
# 5
# 6
# bytes
# True
s = "héllo"
b = s.encode("utf-8")
print(len(s))
print(len(b))
print(type(b).__name__)
print(b.decode("utf-8") == s)
