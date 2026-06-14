# expect:
# abcde
# ABCDE
# 0123456789
# 0123456789
# Hello World Foo

from string import ascii_lowercase, ascii_uppercase, digits, hexdigits, capwords

print(ascii_lowercase[:5])
print(ascii_uppercase[:5])
print(digits)
print(hexdigits[:10])
print(capwords("hello world foo"))
