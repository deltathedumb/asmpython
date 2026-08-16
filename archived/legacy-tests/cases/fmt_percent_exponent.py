# probes: %e interpolates scientific notation
# expect:
# 1.234568e+03
# 1.23e+03
print("%e" % 1234.5678)
print("%.2e" % 1234.5678)
