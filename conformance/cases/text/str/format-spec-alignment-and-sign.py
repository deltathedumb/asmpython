# tier: spec
# ref: library/string.html#format-specification-mini-language
# expect:
# 00042.00
# +42 -42
# 42    |
#   42  |
# 0xff 0o377 0b11111111
# 1,234,567
print(format(42, "08.2f"))
print(format(42, "+d"), format(-42, "+d"))
print(format(42, "<6") + "|")
print(format(42, "^6") + "|")
print(format(255, "#x"), format(255, "#o"), format(255, "#b"))
print(format(1234567, ","))
