# tier: spec
# ref: library/functions.html#repr
# expect:
# 6
# "'caf\xe9'"
# "'caf\\xe9'"
# False
# 4
s = "caf\u00e9"
print(len(repr(s)))
print(ascii(repr(s)))
print(ascii(ascii(s)))
print(repr(s) == ascii(s))
print(len(repr("\n")))
