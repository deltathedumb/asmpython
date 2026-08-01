# probes: mutation survives two parameter hops
# expect:
# [1, 3]
def inner(xs):
    xs.append(3)


def outer(xs):
    inner(xs)


a = [1]
outer(a)
print(a)
