# probes: E renders the exponent marker in upper case
# expect:
# 1.234500E+03
# 1.2E+03
print(format(1234.5, "E"))
print(format(1234.5, ".1E"))
