# tier: spec
# ref: library/stdtypes.html#str.upper
# expect:
# HELLO WORLD hello world
# Hello World Hello world HELLO wORLD
# hello world
# SS ss
s = "hello World"
print(s.upper(), s.lower())
print(s.title(), s.capitalize(), s.swapcase())
print(s.casefold())
print("ß".upper(), "ß".casefold())
print("".upper())
