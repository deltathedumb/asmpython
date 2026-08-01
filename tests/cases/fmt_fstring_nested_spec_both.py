# probes: nested fields supply width and precision together
# expect:
# [     3.14]
width = 9
digits = 2
print("[" + f"{3.14159:>{width}.{digits}f}" + "]")
