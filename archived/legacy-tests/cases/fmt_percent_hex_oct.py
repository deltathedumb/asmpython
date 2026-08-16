# probes: %x and %o interpolate integer bases
# expect:
# ff
# 10
print("%x" % 255)
print("%o" % 8)
