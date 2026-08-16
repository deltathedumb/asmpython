# probes: builtin methods work on an opaque receiver
# expect:
# HI
# 3
# 2
def shout(s):
    return s.upper()


def grow(xs, v):
    xs.append(v)
    return len(xs)


def keys_of(d):
    return len(d)


print(shout("hi"))
print(grow([1, 2], 3))
print(keys_of({"a": 1, "b": 2}))
