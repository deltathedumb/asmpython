# tier: spec
# ref: peps.python.org/pep-0380/
# expect:
# [1, 2, 'done']
def inner():
    yield 1
    yield 2
    return 'done'

def outer():
    r = yield from inner()
    yield r

print(list(outer()))
