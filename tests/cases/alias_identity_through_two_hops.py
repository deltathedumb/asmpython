# probes: identity survives two call hops
# expect:
# True
def inner(v):
    return v


def outer(v):
    return inner(v)


a = {"k": 1}
print(outer(a) is a)
