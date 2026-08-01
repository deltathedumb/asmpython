# tier: spec
# ref: library/stdtypes.html#str.isalnum
# expect:
# 'abc123' True True True False False False
# 'abc' True True True False False False
# '123' True False True False True True
# '  ' False False True False False False
# 'ABC' True True True False False False
# 'abc ' False False True False False False
# 'Abc' True True True True False False
# '²' True False True False True False
for s in ("abc123", "abc", "123", "  ", "ABC", "abc ", "Abc", "\u00b2"):
    print(repr(s), s.isalnum(), s.isidentifier(), s.isprintable(),
          s.istitle(), s.isnumeric(), s.isdecimal())
