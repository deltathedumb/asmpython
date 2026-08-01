# probes: str.format applies the spec after the colon
# expect:
#      x
# 3.14
print("{:>6}".format("x"))
print("{:.2f}".format(3.14159))
