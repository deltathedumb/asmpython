# tier: spec
# ref: library/functions.html#chr
# expect:
# 97 a
# 233 128512
# 1
# ValueError
# TypeError
print(ord("a"), chr(97))
print(ord("\u00e9"), ord("\U0001F600"))
print(len(chr(0x10FFFF)))
try:
    chr(0x110000)
except ValueError:
    print("ValueError")
try:
    ord("ab")
except TypeError:
    print("TypeError")
