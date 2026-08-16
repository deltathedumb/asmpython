# probes: a bare float keeps its decimal point
# expect:
# 2.0
# 2.0
print(format(2.0, ""))
print(str(2.0))
