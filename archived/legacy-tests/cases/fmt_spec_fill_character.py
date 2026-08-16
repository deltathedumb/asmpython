# probes: a fill character precedes the alignment
# expect:
# ****x
# 0007
print(format("x", "*>5"))
print(format(7, "0>4"))
