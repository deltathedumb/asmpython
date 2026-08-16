# probes: float survives an opaque round trip
# expect:
# 1.5
# 1.75
def passthrough(v):
    return v


f = passthrough(1.5)
print(f)
print(f + 0.25)
