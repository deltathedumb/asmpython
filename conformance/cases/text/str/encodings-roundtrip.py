# tier: spec
# ref: library/stdtypes.html#str.encode
# expect:
# utf-8 6 True
# utf-16 8 True
# utf-32 16 True
# UnicodeEncodeError
# b'a??'
s = "a\u00e9\u4e2d"
for enc in ("utf-8", "utf-16", "utf-32"):
    b = s.encode(enc)
    print(enc, len(b), b.decode(enc) == s)
try:
    s.encode("ascii")
except UnicodeEncodeError:
    print("UnicodeEncodeError")
print(ascii(s.encode("ascii", "replace")))
