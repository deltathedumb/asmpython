# probes: grouping survives a minus sign
# expect:
# -1,234,567
# -1,234.5
print(format(-1234567, ","))
print(format(-1234.5, ",.1f"))
