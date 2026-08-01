# tier: spec
# ref: library/string.html#format-specification-mini-language
# expect:
# 1,234,567
# 1_234_567
# 1,234,567.89
# 1,234,567
# ff
print(format(1234567, ","))
print(format(1234567, "_"))
print(format(1234567.891, ",.2f"))
print(f"{1234567:,}")
print(format(255, "_x"))
