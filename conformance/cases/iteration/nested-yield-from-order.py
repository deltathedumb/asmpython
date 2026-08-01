# tier: spec
# ref: reference/expressions.html#yield-expressions
# expect:
# [0, 1, 2, 3]
def leaf():
    yield 1
    yield 2

def middle():
    yield 0
    yield from leaf()
    yield 3

print(list(middle()))
