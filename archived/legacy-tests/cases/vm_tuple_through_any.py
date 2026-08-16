# probes: tuple survives an opaque round trip
# expect:
# 1
# a
# 2
def passthrough(v):
    return v


t = passthrough((1, "a"))
print(t[0])
print(t[1])
print(len(t))
