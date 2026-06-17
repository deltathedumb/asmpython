# expect:
# 26
# 26
# 52
# 10
# False
# True
# Hello World

import string

al: str = string.ascii_lowercase
au: str = string.ascii_uppercase
al2: str = string.ascii_letters
dig: str = string.digits

print(len(al))
print(len(au))
print(len(al2))
print(len(dig))
print("a" in dig)
print("a" in al)
print(string.capwords("hello world"))
