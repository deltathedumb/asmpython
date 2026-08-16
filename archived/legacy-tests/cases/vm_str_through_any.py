# probes: str survives an opaque round trip
# expect:
# hello
# 5
# HELLO
def passthrough(v):
    return v


s = passthrough("hello")
print(s)
print(len(s))
print(s.upper())
