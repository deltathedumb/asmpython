# tier: spec
# ref: reference/expressions.html#yield-expressions
# expect:
# inner-1
# ('inner-got', 'x')
# ('outer-saw', 'inner-return')
def inner():
    got = yield "inner-1"
    yield ("inner-got", got)
    return "inner-return"

def outer():
    result = yield from inner()
    yield ("outer-saw", result)

g = outer()
print(next(g))
print(g.send("x"))
print(next(g))
