# probes: % scales by 100 and appends a sign
# expect:
# 25.000000%
# 25.0%
print(format(0.25, "%"))
print(format(0.25, ".1%"))
