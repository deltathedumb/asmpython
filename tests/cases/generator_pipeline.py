# expect:
# [0, 4, 8, 12, 16]
def evens(seq):
    for x in seq:
        if x % 2 == 0:
            yield x
def doubled(seq):
    for x in seq:
        yield x * 2
print(list(doubled(evens(range(10)))))
# asmpython (beta/3.14.0) rejects at compile: [E022] list() requires a list, tuple, dict, or string
