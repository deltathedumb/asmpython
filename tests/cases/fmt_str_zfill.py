# probes: zfill pads a numeric string with zeros
# expect:
# 00042
# -0042
print("42".zfill(5))
print("-42".zfill(5))
