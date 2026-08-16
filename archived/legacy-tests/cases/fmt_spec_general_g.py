# probes: g picks fixed or scientific by magnitude
# expect:
# 1234.57
# 1.234e-05
# 1.23e+03
print(format(1234.5678, "g"))
print(format(0.00001234, "g"))
print(format(1234.5678, ".3g"))
