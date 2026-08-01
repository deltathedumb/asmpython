# probes: str.format supports a nested spec field
# expect:
#      x
print("{0:{1}}".format("x", ">6"))
