# tier: spec
# ref: library/stdtypes.html#text-sequence-type-str
# expect:
# a
# str
# c
# cba
# 97 b
s = "abc"
print(s[0])
print(type(s[0]).__name__)
print(s[-1])
print(s[::-1])
print(ord("a"), chr(98))
