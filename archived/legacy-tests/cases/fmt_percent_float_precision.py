# probes: %.Nf fixes the fraction digits
# expect:
# 3.14
# 2
print("%.2f" % 3.14159)
print("%.0f" % 2.5)
