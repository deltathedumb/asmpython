# tier: spec
# ref: reference/expressions.html#yield-expressions
# expect:
# 1
# a
# ('a', 'b')
def gen():
    x = yield 1
    y = yield x
    return (x, y)

g = gen()
print(next(g))
print(g.send("a"))
try:
    g.send("b")
except StopIteration as e:
    print(e.value)
