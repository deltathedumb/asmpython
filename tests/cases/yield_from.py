# expect:
# [1, 2, 3]
def inner():
    yield 1
    yield 2
def outer():
    yield from inner()
    yield 3
print(list(outer()))
# asmpython (beta/3.14.0) rejects at compile: [E022] list() requires a list, tuple, dict, or string
