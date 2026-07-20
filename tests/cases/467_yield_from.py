# expect:
# 1
# 2
# Delegated generator iteration regression; preserves yielded order.


def inner():
    yield 1
    yield 2


def outer():
    yield from inner()


for value in outer():
    print(value)
