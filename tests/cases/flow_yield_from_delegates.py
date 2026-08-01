# probes: yield from forwards a sub-generator's values
# expect:
# [0, 1, 2, 3]
def inner():
    yield 1
    yield 2


def outer():
    yield 0
    yield from inner()
    yield 3


print(list(outer()))
