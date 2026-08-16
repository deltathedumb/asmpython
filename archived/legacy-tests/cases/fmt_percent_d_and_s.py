# probes: %d and %s interpolate int and str
# expect:
# 3 items
# many items
print("%d items" % 3)
print("%s items" % "many")
