# tier: spec
# ref: reference/lexical_analysis.html#string-and-bytes-literals
# expect:
# bytes str
# False
# TypeError
# True True
# 97 a
b = b"abc"
s = "abc"
print(type(b).__name__, type(s).__name__)
print(b == s)
try:
    b + s
except TypeError:
    print("TypeError")
print(b.decode() == s, s.encode() == b)
print(b[0], s[0])
