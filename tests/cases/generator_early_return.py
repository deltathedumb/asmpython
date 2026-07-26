# expect:
# [1, 2]
def take_until_neg(seq):
    for x in seq:
        if x < 0:
            return
        yield x
print(list(take_until_neg([1, 2, -1, 3])))
# asmpython (beta/3.14.0) rejects at compile: [E022] list() requires a list, tuple, dict, or string
