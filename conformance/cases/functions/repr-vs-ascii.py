# tier: spec
# ref: library/functions.html#ascii
# expect:
# 'aé'
# 'a\xe9'
# ['a\xe9']
# b'a'
# 1 1
s = "a\u00e9"
print(repr(s))
print(ascii(s))
print(ascii([s]))
print(repr(b"a"))
print(ascii(1), repr(1))
