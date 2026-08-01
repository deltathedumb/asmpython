# tier: spec
# ref: library/stdtypes.html#str.isalpha
# expect:
# 'abc' True False False False
# 'ab1' False False False False
# '123' False True False False
# ' ' False False True False
# '' False False False False
# 'AB' True False False True
for s in ("abc", "ab1", "123", " ", "", "AB"):
    print(repr(s), s.isalpha(), s.isdigit(), s.isspace(), s.isupper())
