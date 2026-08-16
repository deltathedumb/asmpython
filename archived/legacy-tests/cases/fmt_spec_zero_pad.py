# probes: a leading 0 zero-pads a number
# expect:
# 00042
# -0042
print(format(42, "05"))
print(format(-42, "05"))
