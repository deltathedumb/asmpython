# tier: spec
# ref: library/string.html#format-specification-mini-language
# expect:
# ***ab***|
# ab******|
# ******ab|
# 3.14
#      3.142|
# -     42
# abc
print(format("ab", "*^8") + "|")
print(format("ab", "*<8") + "|")
print(format("ab", "*>8") + "|")
print(format(3.14159, ".3"))
print(format(3.14159, "10.3f") + "|")
print(format(-42, "=+8d"))
print(format("abcdef", ".3"))
