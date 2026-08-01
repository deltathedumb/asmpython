# tier: spec
# ref: library/string.html#format-specification-mini-language
# expect:
# 1.234000e+03
# 1.23e+03
# 50.000000%
# 50.0%
# 1234 1.2345e-05
# True
# 1234
print(format(1234, "e"))
print(format(1234, ".2e"))
print(format(0.5, "%"))
print(format(0.5, ".1%"))
print(format(1234, "g"), format(0.000012345, "g"))
print(format(255, "c") == chr(255))
print(format(1234, "n"))
