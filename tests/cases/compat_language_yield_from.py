# guards: language_compat_fixes
# expect:
# 0
# 1
# 2
# 3
def inner():
    yield 1
    yield 2


def outer():
    yield 0
    yield from inner()
    yield 3


for v in outer():
    print(v)
