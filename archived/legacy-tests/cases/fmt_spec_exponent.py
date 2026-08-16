# probes: e renders scientific notation
# expect:
# 1.234568e+03
# 1.23e+03
print(format(1234.5678, "e"))
print(format(1234.5678, ".2e"))
