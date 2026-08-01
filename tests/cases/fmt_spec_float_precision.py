# probes: .Nf fixes the fraction digits
# expect:
# 3.14
# 2.000
# 1.0
print(format(3.14159, ".2f"))
print(format(2.0, ".3f"))
print(format(1.005, ".1f"))
